"""达标率(超标率)统计口径: 参与考核的因子与时段, 以及新口径的生效时间.

达标率 = 1 - 超标率, 超标率 = 超标记录数 / 参与考核记录数.
只有设定了限值的 (因子, 时段) 组合才参与考核; 未设限值的记录
(如 PM2.5 / PM10 的 1 小时均值) 仅存档记录, 不计入分母.

新口径自 DEFAULT_RATE_RULE_EFFECTIVE_FROM 起生效; 此前月份的达标率已对外
发布, 相关历史记录仍按旧口径(全部录入记录计入分母)统计, 不做重算.
"""
from datetime import date, datetime, time

from .constants import PERIOD_LABELS
from .standards import POLLUTANTS, get_limit

# 新达标率口径的默认生效日期, 可被配置项 RATE_RULE_EFFECTIVE_FROM 覆盖
DEFAULT_RATE_RULE_EFFECTIVE_FROM = date(2026, 9, 1)


def is_assessable(pollutant_code, period):
    """该 (因子, 时段) 是否设有限值, 即是否参与达标率考核."""
    return get_limit(pollutant_code, period) is not None


def assessable_pairs():
    """所有参与达标率考核的 (因子, 时段) 组合."""
    return [
        (code, period)
        for code, meta in POLLUTANTS.items()
        for period, limit in meta["limits"].items()
        if limit is not None
    ]


def parse_effective_from(raw=None):
    """Normalize the configured effective date to a datetime (start of day)."""
    if raw in (None, ""):
        raw = DEFAULT_RATE_RULE_EFFECTIVE_FROM
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, date):
        return datetime.combine(raw, time.min)
    return datetime.strptime(str(raw).strip()[:10], "%Y-%m-%d")


def scope_payload(effective_from=None):
    """达标率考核口径说明, 随统计接口返回, 便于界面展示与对外解释."""
    effective_from = parse_effective_from(effective_from)
    by_period = {}
    skipped = []
    for code, meta in POLLUTANTS.items():
        for period, limit in meta["limits"].items():
            if limit is None:
                skipped.append("%s%s" % (meta["label"], PERIOD_LABELS.get(period, period)))
            else:
                by_period.setdefault(period, []).append(meta["label"])
    return {
        "effective_from": effective_from.date().isoformat(),
        "assessable": [
            {
                "period": period,
                "period_label": PERIOD_LABELS.get(period, period),
                "pollutants": pollutants,
            }
            for period, pollutants in by_period.items()
        ],
        "not_assessable": skipped,
        "legacy_note": (
            "%s 之前的历史记录沿用旧口径(全部记录计入分母), "
            "已对外发布的历史月份达标率不重算" % effective_from.date().isoformat()
        ),
    }
