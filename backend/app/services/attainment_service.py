"""达标率考核口径与已发布月度快照.

考核口径 (assessed_only): 只有标准中设定了限值的“因子 + 数据周期”组合参与
达标率/超标率计算 (PM2.5、PM10 小时均值未设限值, 仅记录不考核)。数据表中以
``Measurement.limit_value IS NOT NULL`` 为准——限值在写入时快照, 历史记录
不因标准调整而改变口径。

已发布月份: 对外发布过的月度达标率以 ``published_rates`` 快照为准, 不参与重算。
"""
import re
from datetime import datetime

from sqlalchemy import cast, func

from ..errors import ConflictError, ValidationError
from ..extensions import db
from ..models import Measurement, PublishedRate
from ..models.published_rate import RULE_ASSESSED_ONLY, RULE_LEGACY_TOTAL

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def parse_month(raw, field="month"):
    """Validate a YYYY-MM month string and return (year, month)."""
    value = str(raw or "").strip()
    if not MONTH_PATTERN.match(value):
        raise ValidationError(
            "月份格式应为 YYYY-MM, 如 2026-08", fields={field: "invalid_month"}
        )
    year, month = int(value[:4]), int(value[5:7])
    return year, month


def _month_bounds(year, month):
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def month_key(value):
    """Format a datetime as the YYYY-MM bucket key used by published rates."""
    return "%04d-%02d" % (value.year, value.month)


def compute_month_rates(month, rule=RULE_ASSESSED_ONLY):
    """Compute network-wide counters for one month under the given rule.

    ``legacy_total`` 为调整前旧口径: 分母 = 当月全部记录(含无限值仅记录数据);
    ``assessed_only`` 为现行口径: 分母 = 当月参与考核(有限值)的记录。
    """
    year, mon = parse_month(month)
    start, end = _month_bounds(year, mon)
    total, assessed, exceeded = (
        db.session.query(
            func.count(Measurement.id),
            func.sum(cast(Measurement.limit_value.isnot(None), db.Integer)),
            func.sum(cast(Measurement.is_exceeded, db.Integer)),
        )
        .filter(Measurement.measured_at >= start, Measurement.measured_at < end)
        .one()
    )
    total = int(total or 0)
    assessed = int(assessed or 0)
    exceeded = int(exceeded or 0)
    denominator = total if rule == RULE_LEGACY_TOTAL else assessed
    exceed_rate = round(exceeded / denominator, 4) if denominator else 0.0
    return {
        "month": month,
        "rule": rule,
        "total_count": total,
        "assessed_count": assessed,
        "not_assessed_count": total - assessed,
        "exceeded_count": exceeded,
        "exceed_rate": exceed_rate,
        "attainment_rate": round(1 - exceed_rate, 4) if denominator else 1.0,
    }


def get_published(month):
    return PublishedRate.query.filter_by(month=month).first()


def published_map():
    """{month: PublishedRate} lookup used by the monthly statistics view."""
    return {row.month: row for row in PublishedRate.query.all()}


def list_published():
    return [row.to_dict() for row in PublishedRate.query.order_by(PublishedRate.month).all()]


def publish_month(month, published_by=None, note=None, rule=RULE_ASSESSED_ONLY):
    """Freeze the attainment rate of one month; published months are never recomputed."""
    year, mon = parse_month(month)
    now = datetime.now()
    if (year, mon) > (now.year, now.month):
        raise ValidationError("不能发布未来月份的达标率", fields={"month": "future_month"})
    if get_published(month) is not None:
        raise ConflictError("%s 达标率已发布, 已发布月份不重算" % month)

    rates = compute_month_rates(month, rule=rule)
    record = PublishedRate(
        month=month,
        rule=rule,
        total_count=rates["total_count"],
        assessed_count=rates["assessed_count"],
        not_assessed_count=rates["not_assessed_count"],
        exceeded_count=rates["exceeded_count"],
        exceed_rate=rates["exceed_rate"],
        attainment_rate=rates["attainment_rate"],
        published_at=now,
        published_by=published_by,
        note=note,
    )
    db.session.add(record)
    db.session.commit()
    return record


def freeze_legacy_months(note=None):
    """一次性迁移: 把已有数据的历史月份按旧口径快照, 保护已对外发布的达标率。

    只处理早于当前月且尚未发布的月份, 返回新冻结的月份清单。
    """
    now = datetime.now()
    current_key = month_key(now)
    rows = (
        db.session.query(
            func.extract("year", Measurement.measured_at),
            func.extract("month", Measurement.measured_at),
        )
        .group_by(
            func.extract("year", Measurement.measured_at),
            func.extract("month", Measurement.measured_at),
        )
        .all()
    )
    months = {"%04d-%02d" % (int(year), int(mon)) for year, mon in rows}
    frozen = []
    for month in sorted(m for m in months if m and m < current_key):
        if get_published(month) is not None:
            continue
        record = publish_month(
            month,
            published_by="system",
            note=note or "历史月份对外发布达标率存档(旧口径)",
            rule=RULE_LEGACY_TOTAL,
        )
        frozen.append(record.to_dict())
    return frozen
