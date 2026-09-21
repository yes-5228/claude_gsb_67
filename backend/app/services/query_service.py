"""监测数据查询: 过滤条件解析, 统计聚合与导出数据准备."""
from datetime import datetime, time

from sqlalchemy import cast, func, or_

from ..domain.constants import (
    DATA_SOURCE_LABELS,
    EXCEEDANCE_STATUS_LABELS,
    PERIOD_LABELS,
    STATION_TYPE_LABELS,
)
from ..domain.standards import POLLUTANT_CODES, get_pollutant
from ..errors import ValidationError
from ..extensions import db
from ..models import Exceedance, Measurement, Station
from ..models.base import iso
from ..utils.validation import parse_date
from . import attainment_service

GROUP_BY_CHOICES = ("station", "area", "pollutant", "period", "day", "month", "data_source")
METRIC_CHOICES = ("avg", "max", "min", "count", "sum")
SORT_CHOICES = ("measured_at", "value", "exceed_ratio", "pollutant", "station_code", "created_at")


def _split(value):
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _int_list(args, name):
    values = []
    for item in _split(args.get(name)):
        try:
            values.append(int(item))
        except ValueError:
            raise ValidationError("%s 参数必须为整数" % name, fields={name: "invalid_integer"})
    return values


def _float_arg(args, name):
    raw = args.get(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValidationError("%s 参数必须为数字" % name, fields={name: "invalid_number"})


def _bool_arg(args, name):
    raw = args.get(name)
    if raw in (None, ""):
        return None
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _date_arg(args, name, end_of_day=False):
    raw = args.get(name)
    if raw in (None, ""):
        return None
    parsed = parse_date(raw, name)
    return datetime.combine(parsed, time.max if end_of_day else time.min)


def parse_filters(args):
    """Translate request args into a normalised filter dictionary."""
    pollutants = [item.upper() for item in _split(args.get("pollutant"))]
    unknown = [item for item in pollutants if item not in POLLUTANT_CODES]
    if unknown:
        raise ValidationError(
            "未知监测因子: %s" % ", ".join(unknown), fields={"pollutant": "unknown"}
        )

    periods = _split(args.get("period"))
    for period in periods:
        if period not in PERIOD_LABELS:
            raise ValidationError("未知数据周期: %s" % period, fields={"period": "unknown"})

    filters = {
        "station_ids": _int_list(args, "station_id"),
        "areas": _split(args.get("area")),
        "station_types": _split(args.get("station_type")),
        "pollutants": pollutants,
        "periods": periods,
        "data_sources": _split(args.get("data_source")),
        "is_exceeded": _bool_arg(args, "is_exceeded"),
        "exceedance_status": _split(args.get("exceedance_status")),
        "date_from": _date_arg(args, "date_from"),
        "date_to": _date_arg(args, "date_to", end_of_day=True),
        "min_value": _float_arg(args, "min_value"),
        "max_value": _float_arg(args, "max_value"),
        "keyword": (args.get("keyword") or "").strip(),
        "recorder": (args.get("recorder") or "").strip(),
    }
    if filters["date_from"] and filters["date_to"] and filters["date_from"] > filters["date_to"]:
        raise ValidationError(
            "开始时间不能晚于结束时间", fields={"date_from": "range_invalid"}
        )
    if (
        filters["min_value"] is not None
        and filters["max_value"] is not None
        and filters["min_value"] > filters["max_value"]
    ):
        raise ValidationError("最小值不能大于最大值", fields={"min_value": "range_invalid"})
    return filters


def apply_filters(query, filters):
    query = query.join(Station, Measurement.station_id == Station.id)
    if filters["station_ids"]:
        query = query.filter(Measurement.station_id.in_(filters["station_ids"]))
    if filters["areas"]:
        query = query.filter(Station.area.in_(filters["areas"]))
    if filters["station_types"]:
        query = query.filter(Station.station_type.in_(filters["station_types"]))
    if filters["pollutants"]:
        query = query.filter(Measurement.pollutant.in_(filters["pollutants"]))
    if filters["periods"]:
        query = query.filter(Measurement.period.in_(filters["periods"]))
    if filters["data_sources"]:
        query = query.filter(Measurement.data_source.in_(filters["data_sources"]))
    if filters["is_exceeded"] is not None:
        query = query.filter(Measurement.is_exceeded.is_(filters["is_exceeded"]))
    if filters["date_from"]:
        query = query.filter(Measurement.measured_at >= filters["date_from"])
    if filters["date_to"]:
        query = query.filter(Measurement.measured_at <= filters["date_to"])
    if filters["min_value"] is not None:
        query = query.filter(Measurement.value >= filters["min_value"])
    if filters["max_value"] is not None:
        query = query.filter(Measurement.value <= filters["max_value"])
    if filters["recorder"]:
        query = query.filter(Measurement.recorder.like("%" + filters["recorder"] + "%"))
    if filters["keyword"]:
        like = "%" + filters["keyword"] + "%"
        query = query.filter(
            or_(Station.name.like(like), Station.code.like(like), Station.address.like(like))
        )
    if filters["exceedance_status"]:
        query = query.join(Exceedance, Exceedance.measurement_id == Measurement.id).filter(
            Exceedance.status.in_(filters["exceedance_status"])
        )
    return query


def apply_sort(query, sort=None, order="desc"):
    sort = sort if sort in SORT_CHOICES else "measured_at"
    column = {
        "measured_at": Measurement.measured_at,
        "value": Measurement.value,
        "exceed_ratio": Measurement.exceed_ratio,
        "pollutant": Measurement.pollutant,
        "station_code": Station.code,
        "created_at": Measurement.created_at,
    }[sort]
    primary = column.desc() if (order or "desc").lower() == "desc" else column.asc()
    return query.order_by(primary, Measurement.id.desc())


def measurement_query(args):
    filters = parse_filters(args)
    query = apply_filters(db.session.query(Measurement), filters)
    return apply_sort(query, args.get("sort"), args.get("order")), filters


def summary(filters):
    """Aggregate counters shown above the query result table.

    达标率/超标率的分母只含参与考核(有限值)的记录; 无限值仅记录的数据
    单独计入 ``not_assessed_count``, 不进分母。
    """
    query = apply_filters(
        db.session.query(
            func.count(Measurement.id),
            func.sum(cast(Measurement.is_exceeded, db.Integer)),
            func.sum(cast(Measurement.limit_value.isnot(None), db.Integer)),
            func.count(func.distinct(Measurement.station_id)),
            func.min(Measurement.measured_at),
            func.max(Measurement.measured_at),
            func.avg(Measurement.value),
        ),
        filters,
    )
    total, exceeded, assessed, stations, first_at, last_at, avg_value = query.one()
    total = int(total or 0)
    exceeded = int(exceeded or 0)
    assessed = int(assessed or 0)
    exceed_rate = round(exceeded / assessed, 4) if assessed else 0.0
    return {
        "total": total,
        "assessed_count": assessed,
        "not_assessed_count": total - assessed,
        "exceeded_count": exceeded,
        "exceed_rate": exceed_rate,
        "attainment_rate": round(1 - exceed_rate, 4) if assessed else 1.0,
        "station_count": int(stations or 0),
        "first_measured_at": iso(first_at),
        "last_measured_at": iso(last_at),
        "avg_value": round(float(avg_value), 2) if avg_value is not None else None,
    }


def _metric_expression(metric):
    return {
        "avg": func.avg(Measurement.value),
        "max": func.max(Measurement.value),
        "min": func.min(Measurement.value),
        "count": func.count(Measurement.id),
        "sum": func.sum(Measurement.value),
    }[metric]


def _is_network_scope(filters):
    """True when no narrowing filter is applied (全网口径).

    已发布月度达标率按全网口径存档, 仅在未加筛选的按月统计中套用,
    带筛选条件的统计始终按实时数据计算。
    """
    return not any(
        [
            filters["station_ids"],
            filters["areas"],
            filters["station_types"],
            filters["pollutants"],
            filters["periods"],
            filters["data_sources"],
            filters["exceedance_status"],
            filters["is_exceeded"] is not None,
            filters["date_from"],
            filters["date_to"],
            filters["min_value"] is not None,
            filters["max_value"] is not None,
            filters["keyword"],
            filters["recorder"],
        ]
    )


def _published_item(snapshot):
    """Build a statistics row from a published monthly snapshot (不再重算)."""
    return {
        "key": snapshot.month,
        "label": snapshot.month,
        "value": None,
        "count": snapshot.total_count,
        "assessed_count": snapshot.assessed_count,
        "not_assessed_count": snapshot.not_assessed_count,
        "exceeded_count": snapshot.exceeded_count,
        "exceed_rate": snapshot.exceed_rate,
        "attainment_rate": snapshot.attainment_rate,
        "published": True,
        "published_rule": snapshot.rule,
        "published_at": iso(snapshot.published_at),
    }


def statistics(args):
    """Grouped aggregation used by the query page statistics panel."""
    filters = parse_filters(args)
    group_by = args.get("group_by") or "pollutant"
    metric = args.get("metric") or "avg"
    if group_by not in GROUP_BY_CHOICES:
        raise ValidationError(
            "group_by 仅支持: %s" % ", ".join(GROUP_BY_CHOICES), fields={"group_by": "unknown"}
        )
    if metric not in METRIC_CHOICES:
        raise ValidationError(
            "metric 仅支持: %s" % ", ".join(METRIC_CHOICES), fields={"metric": "unknown"}
        )

    value_expr = _metric_expression(metric).label("metric_value")
    count_expr = func.count(Measurement.id).label("row_count")
    exceeded_expr = func.sum(cast(Measurement.is_exceeded, db.Integer)).label("exceeded_count")
    assessed_expr = func.sum(cast(Measurement.limit_value.isnot(None), db.Integer)).label(
        "assessed_count"
    )

    if group_by == "station":
        query = db.session.query(
            Station.id.label("station_id"),
            Station.code.label("station_code"),
            Station.name.label("station_name"),
            Station.area.label("area"),
            value_expr,
            count_expr,
            exceeded_expr,
            assessed_expr,
        ).group_by(Station.id, Station.code, Station.name, Station.area)
        is_time_group = False
    elif group_by == "area":
        query = db.session.query(
            Station.area.label("area"), value_expr, count_expr, exceeded_expr, assessed_expr
        ).group_by(Station.area)
        is_time_group = False
    elif group_by == "day":
        bucket = func.date(Measurement.measured_at).label("bucket")
        query = db.session.query(
            bucket, value_expr, count_expr, exceeded_expr, assessed_expr
        ).group_by(bucket)
        is_time_group = True
    elif group_by == "month":
        year = func.extract("year", Measurement.measured_at).label("year")
        month = func.extract("month", Measurement.measured_at).label("month")
        query = db.session.query(
            year, month, value_expr, count_expr, exceeded_expr, assessed_expr
        ).group_by(year, month)
        is_time_group = True
    else:
        column = {
            "pollutant": Measurement.pollutant,
            "period": Measurement.period,
            "data_source": Measurement.data_source,
        }[group_by]
        query = db.session.query(
            column.label("bucket"), value_expr, count_expr, exceeded_expr, assessed_expr
        ).group_by(column)
        is_time_group = False

    query = apply_filters(query, filters)
    rows = query.all()

    items = []
    for row in rows:
        data = dict(row._mapping)
        count = int(data.get("row_count") or 0)
        exceeded = int(data.get("exceeded_count") or 0)
        assessed = int(data.get("assessed_count") or 0)
        raw_value = data.get("metric_value")
        if group_by == "station":
            key = data.get("station_code")
            label = "%s %s" % (data.get("station_code"), data.get("station_name"))
        elif group_by == "area":
            key = label = data.get("area")
        elif group_by == "day":
            key = str(data.get("bucket"))
            label = key
        elif group_by == "month":
            key = "%04d-%02d" % (int(data.get("year")), int(data.get("month")))
            label = key
        elif group_by == "pollutant":
            key = data.get("bucket")
            meta = get_pollutant(key)
            label = meta["label"] if meta else key
        elif group_by == "period":
            key = data.get("bucket")
            label = PERIOD_LABELS.get(key, key)
        else:
            key = data.get("bucket")
            label = DATA_SOURCE_LABELS.get(key, key)

        exceed_rate = round(exceeded / assessed, 4) if assessed else 0.0
        items.append(
            {
                "key": key,
                "label": label,
                "value": round(float(raw_value), 2) if raw_value is not None else None,
                "count": count,
                "assessed_count": assessed,
                "not_assessed_count": count - assessed,
                "exceeded_count": exceeded,
                "exceed_rate": exceed_rate,
                "attainment_rate": round(1 - exceed_rate, 4) if assessed else 1.0,
                "published": False,
            }
        )

    if group_by == "month" and _is_network_scope(filters):
        # 已对外发布的月份以快照为准, 不参与重算
        published = attainment_service.published_map()
        items = [
            _published_item(published[item["key"]]) if item["key"] in published else item
            for item in items
        ]
        covered = {item["key"] for item in items}
        items.extend(
            _published_item(snapshot)
            for month, snapshot in sorted(published.items())
            if month not in covered
        )

    if is_time_group:
        items.sort(key=lambda item: item["key"])
    else:
        items.sort(key=lambda item: (item["value"] is None, -(item["value"] or 0)))

    return {
        "group_by": group_by,
        "metric": metric,
        "items": items,
        "totals": {
            "count": sum(item["count"] for item in items),
            "exceeded_count": sum(item["exceeded_count"] for item in items),
        },
    }


def option_payload():
    return {
        "group_by": list(GROUP_BY_CHOICES),
        "metric": list(METRIC_CHOICES),
        "sort": list(SORT_CHOICES),
        "exceedance_status": [
            {"value": key, "label": label} for key, label in EXCEEDANCE_STATUS_LABELS.items()
        ],
        "station_type": [
            {"value": key, "label": label} for key, label in STATION_TYPE_LABELS.items()
        ],
    }
