"""污染物监测因子与限值定义 (GB 3095-2012 环境空气质量标准, 二级浓度限值)."""

# period 取值: hourly = 1 小时平均, daily = 24 小时平均
POLLUTANTS = {
    "PM25": {
        "code": "PM25",
        "label": "PM2.5",
        "name": "细颗粒物",
        "unit": "μg/m³",
        "precision": 1,
        "limits": {"daily": 75.0, "hourly": None},
    },
    "PM10": {
        "code": "PM10",
        "label": "PM10",
        "name": "可吸入颗粒物",
        "unit": "μg/m³",
        "precision": 1,
        "limits": {"daily": 150.0, "hourly": None},
    },
    "SO2": {
        "code": "SO2",
        "label": "SO₂",
        "name": "二氧化硫",
        "unit": "μg/m³",
        "precision": 1,
        "limits": {"daily": 150.0, "hourly": 500.0},
    },
    "NO2": {
        "code": "NO2",
        "label": "NO₂",
        "name": "二氧化氮",
        "unit": "μg/m³",
        "precision": 1,
        "limits": {"daily": 80.0, "hourly": 200.0},
    },
    "CO": {
        "code": "CO",
        "label": "CO",
        "name": "一氧化碳",
        "unit": "mg/m³",
        "precision": 2,
        "limits": {"daily": 4.0, "hourly": 10.0},
    },
    "O3": {
        "code": "O3",
        "label": "O₃",
        "name": "臭氧",
        "unit": "μg/m³",
        "precision": 1,
        "limits": {"daily": 160.0, "hourly": 200.0},
    },
}

POLLUTANT_CODES = tuple(POLLUTANTS.keys())

PERIODS = ("hourly", "daily")


def get_pollutant(code):
    """Return the pollutant definition or None when unknown."""
    return POLLUTANTS.get(str(code or "").upper())


def get_limit(code, period):
    """Return the concentration limit for a pollutant/period pair (None if undefined)."""
    pollutant = get_pollutant(code)
    if not pollutant:
        return None
    return pollutant["limits"].get(period)


def is_assessable(code, period):
    """Whether a pollutant/period pair has a limit and thus joins the attainment rate.

    PM2.5 / PM10 小时均值未设限值, 对应记录仅存档, 不参与达标率考核。
    """
    return get_limit(code, period) is not None


def assessable_pairs():
    """Serialisable list of (pollutant, period) pairs that join the attainment rate."""
    pairs = []
    for pollutant in POLLUTANTS.values():
        for period in PERIODS:
            limit = pollutant["limits"].get(period)
            pairs.append(
                {
                    "pollutant": pollutant["code"],
                    "pollutant_label": pollutant["label"],
                    "period": period,
                    "assessable": limit is not None,
                    "limit": limit,
                }
            )
    return pairs


def pollutant_options():
    """Serialisable list used by the frontend dropdowns."""
    return [dict(item) for item in POLLUTANTS.values()]
