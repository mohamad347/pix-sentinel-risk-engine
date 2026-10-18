from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import relationship

from models.base import Base


PIX_ANALYSIS_STATUS = ENUM(
    "approved",
    "two_factor_required",
    "rejected",
    name="pix_analysis_status",
)


class PixAnalysis(Base):
    __tablename__ = "pix_analyses"

    analysis_key = Column(UUID(as_uuid=True), primary_key=True)
    transaction_id = Column(
        BigInteger,
        ForeignKey(
            "pix_transactions.id",
            name="fk_pix_analyses_transaction",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    status = Column(PIX_ANALYSIS_STATUS, nullable=False)
    decision_metadata = Column("metadata", JSONB, nullable=False)
    evidence_snapshot = Column(JSONB, nullable=False)
    policy_version = Column(String(64), nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("transaction_id", name="pix_analyses_transaction_id_key"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_pix_analyses_metadata_object"),
        CheckConstraint(
            "jsonb_typeof(evidence_snapshot) = 'object'",
            name="ck_pix_analyses_evidence_object",
        ),
    )

    transaction = relationship("PixTransaction", back_populates="analysis")
