"""达标率统计口径测试: 参与考核范围 / 无限值记录说明 / 历史口径保护."""
from datetime import date, datetime

from app.domain import attainment


def _seed_hourly_pair(client, station, entry_payload, measured_at):
    """同一时刻录入一条无限值记录(PM2.5 小时值)与一条超标记录(SO2 小时值)."""
    return client.post(
        "/api/measurements/entries",
        json=entry_payload(
            station.id,
            measured_at=measured_at,
            period="hourly",
            entries=[{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 900.0}],
        ),
    )


def test_assessable_pairs_match_defined_limits():
    pairs = set(attainment.assessable_pairs())
    assert ("PM25", "daily") in pairs
    assert ("PM10", "daily") in pairs
    assert ("PM25", "hourly") not in pairs
    assert ("PM10", "hourly") not in pairs
    assert ("SO2", "hourly") in pairs
    # 6 因子日均值 + SO2/NO2/CO/O3 小时均值
    assert len(pairs) == 10


def test_is_assessable():
    assert attainment.is_assessable("PM25", "daily") is True
    assert attainment.is_assessable("PM25", "hourly") is False
    assert attainment.is_assessable("PM10", "hourly") is False
    assert attainment.is_assessable("O3", "hourly") is True


def test_parse_effective_from_defaults_and_strings():
    assert attainment.parse_effective_from(None) == datetime(2026, 9, 1)
    assert attainment.parse_effective_from("") == datetime(2026, 9, 1)
    assert attainment.parse_effective_from("2026-10-01") == datetime(2026, 10, 1)
    assert attainment.parse_effective_from(date(2026, 8, 15)) == datetime(2026, 8, 15)
    assert attainment.parse_effective_from(datetime(2026, 9, 1, 8, 30)) == datetime(2026, 9, 1, 8, 30)


def test_scope_payload_describes_assessment_scope():
    payload = attainment.scope_payload()
    assert payload["effective_from"] == "2026-09-01"
    assert sorted(payload["not_assessable"]) == ["PM10小时均值", "PM2.5小时均值"]
    periods = {item["period"]: item["pollutants"] for item in payload["assessable"]}
    assert set(periods["daily"]) == {"PM2.5", "PM10", "SO₂", "NO₂", "CO", "O₃"}
    assert set(periods["hourly"]) == {"SO₂", "NO₂", "CO", "O₃"}
    assert "不重算" in payload["legacy_note"]


def test_summary_excludes_no_limit_records_after_effective_date(client, station, entry_payload):
    _seed_hourly_pair(client, station, entry_payload, "2026-09-10 10:00")
    summary = client.get("/api/query/measurements").get_json()["summary"]
    assert summary["total"] == 2
    assert summary["exceeded_count"] == 1
    # PM2.5 小时值未设限值, 不计入达标率分母, 单独说明
    assert summary["assessed_count"] == 1
    assert summary["not_assessed_count"] == 1
    assert summary["exceed_rate"] == 1.0
    assert summary["attain_rate"] == 0.0
    assert summary["rate_scope"]["effective_from"] == "2026-09-01"


def test_summary_keeps_legacy_denominator_for_history(client, station, entry_payload):
    _seed_hourly_pair(client, station, entry_payload, "2026-08-10 10:00")
    summary = client.get("/api/query/measurements").get_json()["summary"]
    # 生效日期之前的历史记录沿用旧口径(全部记录计入分母), 已发布达标率不被重算
    assert summary["total"] == 2
    assert summary["assessed_count"] == 2
    assert summary["not_assessed_count"] == 0
    assert summary["exceed_rate"] == 0.5
    assert summary["attain_rate"] == 0.5


def test_summary_blends_rules_across_effective_date(client, station, entry_payload):
    _seed_hourly_pair(client, station, entry_payload, "2026-08-10 10:00")
    _seed_hourly_pair(client, station, entry_payload, "2026-09-10 10:00")
    summary = client.get("/api/query/measurements").get_json()["summary"]
    assert summary["total"] == 4
    assert summary["exceeded_count"] == 2
    # 历史 2 条按旧口径全部计入 + 新数据仅 1 条有限值记录计入
    assert summary["assessed_count"] == 3
    assert summary["not_assessed_count"] == 1
    assert summary["exceed_rate"] == round(2 / 3, 4)
    assert summary["attain_rate"] == round(1 / 3, 4)


def test_statistics_report_assessed_base_per_group(client, station, entry_payload):
    _seed_hourly_pair(client, station, entry_payload, "2026-09-10 10:00")
    body = client.get("/api/query/statistics?group_by=pollutant&metric=count").get_json()
    items = {item["key"]: item for item in body["items"]}
    assert items["PM25"]["count"] == 1
    assert items["PM25"]["assessed_count"] == 0
    assert items["PM25"]["not_assessed_count"] == 1
    assert items["PM25"]["exceed_rate"] == 0.0
    assert items["PM25"]["attain_rate"] is None
    assert items["SO2"]["assessed_count"] == 1
    assert items["SO2"]["exceed_rate"] == 1.0
    assert items["SO2"]["attain_rate"] == 0.0
    assert body["totals"]["count"] == 2
    assert body["totals"]["assessed_count"] == 1
    assert body["rate_scope"]["not_assessable"]


def test_preview_summary_counts_no_limit_factors(client, station, entry_payload):
    payload = entry_payload(
        station.id,
        period="hourly",
        entries=[{"pollutant": "PM25", "value": 60.0}, {"pollutant": "SO2", "value": 100.0}],
    )
    payload.pop("station_id")
    body = client.post("/api/measurements/preview", json=payload).get_json()
    assert body["summary"]["total"] == 2
    assert body["summary"]["not_assessed_count"] == 1
