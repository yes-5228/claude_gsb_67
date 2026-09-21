"""已对外发布的月度达标率快照.

历史月份一旦对外发布, 达标率/超标率以快照为准, 后续口径调整或数据订正
都不再回算已发布月份, 避免对外口径前后不一致。
"""
from ..extensions import db
from .base import TimestampMixin, iso

# 口径标识: legacy_total = 调整前旧口径(分母含无限值记录), assessed_only = 仅考核有限值记录
RULE_LEGACY_TOTAL = "legacy_total"
RULE_ASSESSED_ONLY = "assessed_only"

RULE_LABELS = {
    RULE_LEGACY_TOTAL: "旧口径(分母含无限值记录)",
    RULE_ASSESSED_ONLY: "新口径(仅考核有限值记录)",
}


class PublishedRate(TimestampMixin, db.Model):
    __tablename__ = "published_rates"

    id = db.Column(db.Integer, primary_key=True)
    month = db.Column(db.String(7), nullable=False, unique=True, index=True)  # YYYY-MM
    rule = db.Column(db.String(32), nullable=False, default=RULE_ASSESSED_ONLY)
    total_count = db.Column(db.Integer, nullable=False, default=0)
    assessed_count = db.Column(db.Integer, nullable=False, default=0)
    not_assessed_count = db.Column(db.Integer, nullable=False, default=0)
    exceeded_count = db.Column(db.Integer, nullable=False, default=0)
    exceed_rate = db.Column(db.Float, nullable=False, default=0.0)
    attainment_rate = db.Column(db.Float, nullable=False, default=1.0)
    published_at = db.Column(db.DateTime, nullable=False)
    published_by = db.Column(db.String(64))
    note = db.Column(db.String(255))

    def to_dict(self):
        return {
            "id": self.id,
            "month": self.month,
            "rule": self.rule,
            "rule_label": RULE_LABELS.get(self.rule, self.rule),
            "total_count": self.total_count,
            "assessed_count": self.assessed_count,
            "not_assessed_count": self.not_assessed_count,
            "exceeded_count": self.exceeded_count,
            "exceed_rate": self.exceed_rate,
            "attainment_rate": self.attainment_rate,
            "published_at": iso(self.published_at),
            "published_by": self.published_by,
            "note": self.note,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }

    def __repr__(self):
        return "<PublishedRate %s %.4f>" % (self.month, self.attainment_rate)
