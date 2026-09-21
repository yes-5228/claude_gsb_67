"""已发布月度达标率: 快照冻结与历史月份不重算."""
from datetime import datetime

from app.models import PublishedRate
from app.services import attainment_service


def _last_month():
    today = datetime.now()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return year, month


def _next_month():
    today = datetime.now()
    year, month = today.year, today.month + 1
    if month == 13:
        year, month = year + 1, 1
    return year, month


def _seed_month(client, station, entry_payload, year, month, day=10):
    """一组小时数据: PM2.5 无限值仅记录 + SO2 超标 + CO 达标."""
    return client.post(
        "/api/measurements/entries",
        json=entry_payload(
            station.id,
            measured_at="%04d-%02d-%02d 08:00" % (year, month, day),
            entries=[
                {"pollutant": "PM25", "value": 60.0},
                {"pollutant": "SO2", "value": 900.0},
                {"pollutant": "CO", "value": 1.2},
            ],
        ),
    )


def test_publish_freezes_month_and_statistics_use_snapshot(client, station, entry_payload):
    year, month = _last_month()
    key = "%04d-%02d" % (year, month)
    _seed_month(client, station, entry_payload, year, month)

    response = client.post(
        "/api/query/attainment/publish",
        json={"month": key, "published_by": "考核办", "note": "月度考核结果已对外公布"},
    )
    assert response.status_code == 201
    snapshot = response.get_json()
    assert snapshot["month"] == key
    assert snapshot["rule"] == "assessed_only"
    assert snapshot["total_count"] == 3
    assert snapshot["assessed_count"] == 2
    assert snapshot["not_assessed_count"] == 1
    assert snapshot["exceeded_count"] == 1
    assert snapshot["exceed_rate"] == 0.5
    assert snapshot["attainment_rate"] == 0.5

    # 发布后再补录同月数据, 按月统计仍返回快照, 不重算
    _seed_month(client, station, entry_payload, year, month, day=11)
    body = client.get("/api/query/statistics?group_by=month&metric=count").get_json()
    item = {row["key"]: row for row in body["items"]}[key]
    assert item["published"] is True
    assert item["published_rule"] == "assessed_only"
    assert item["count"] == 3
    assert item["assessed_count"] == 2
    assert item["exceed_rate"] == 0.5
    assert item["attainment_rate"] == 0.5


def test_unpublished_month_uses_live_assessed_rate(client, station, entry_payload):
    year, month = _last_month()
    key = "%04d-%02d" % (year, month)
    _seed_month(client, station, entry_payload, year, month)

    body = client.get("/api/query/statistics?group_by=month&metric=count").get_json()
    item = {row["key"]: row for row in body["items"]}[key]
    assert item["published"] is False
    assert item["count"] == 3
    assert item["assessed_count"] == 2
    assert item["not_assessed_count"] == 1
    assert item["exceed_rate"] == 0.5


def test_filtered_statistics_ignore_published_snapshot(client, station, entry_payload):
    year, month = _last_month()
    key = "%04d-%02d" % (year, month)
    _seed_month(client, station, entry_payload, year, month)
    client.post("/api/query/attainment/publish", json={"month": key})

    body = client.get(
        "/api/query/statistics?group_by=month&metric=count&pollutant=SO2"
    ).get_json()
    item = {row["key"]: row for row in body["items"]}[key]
    assert item["published"] is False
    assert item["count"] == 1


def test_publish_rejects_invalid_or_future_month(client):
    assert client.post("/api/query/attainment/publish", json={}).status_code == 422
    assert client.post("/api/query/attainment/publish", json={"month": "2026-8"}).status_code == 422
    assert client.post("/api/query/attainment/publish", json={"month": "2026-13"}).status_code == 422
    future = "%04d-%02d" % _next_month()
    assert client.post("/api/query/attainment/publish", json={"month": future}).status_code == 422


def test_publish_twice_is_conflict(client, station, entry_payload):
    year, month = _last_month()
    key = "%04d-%02d" % (year, month)
    _seed_month(client, station, entry_payload, year, month)
    assert client.post("/api/query/attainment/publish", json={"month": key}).status_code == 201
    assert client.post("/api/query/attainment/publish", json={"month": key}).status_code == 409


def test_published_listing(client, station, entry_payload):
    year, month = _last_month()
    key = "%04d-%02d" % (year, month)
    _seed_month(client, station, entry_payload, year, month)
    client.post("/api/query/attainment/publish", json={"month": key, "published_by": "考核办"})

    body = client.get("/api/query/attainment/published").get_json()
    assert [item["month"] for item in body["items"]] == [key]
    assert body["items"][0]["published_by"] == "考核办"
    assert body["items"][0]["rule_label"]


def test_freeze_legacy_months_uses_legacy_denominator(client, station, entry_payload):
    """一次性迁移: 历史月份按旧口径(分母含无限值记录)冻结, 且幂等."""
    year, month = _last_month()
    key = "%04d-%02d" % (year, month)
    _seed_month(client, station, entry_payload, year, month)

    frozen = attainment_service.freeze_legacy_months()
    assert [item["month"] for item in frozen] == [key]
    snapshot = PublishedRate.query.filter_by(month=key).one()
    assert snapshot.rule == "legacy_total"
    assert snapshot.total_count == 3
    assert snapshot.exceed_rate == round(1 / 3, 4)

    # 已冻结的月份不会重复处理
    assert attainment_service.freeze_legacy_months() == []


def test_freeze_legacy_months_skips_current_month(client, station, entry_payload):
    client.post("/api/measurements/entries", json=entry_payload(station.id))
    assert attainment_service.freeze_legacy_months() == []
    assert PublishedRate.query.count() == 0
