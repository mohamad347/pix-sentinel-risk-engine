from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from models.base import Base


class PixAnalysis(Base):
    __tablename__ = "pix_analyses"

    analysis_key = Column(UUID(as_uuid=True), primary_key=True)
    transaction_id = Column(BigInteger, ForeignKey("pix_transactions.id"), nullable=False)
    status = Column(String, nullable=False)
    decision_metadata = Column("metadata", JSONB, nullable=False)
    evidence_snapshot = Column(JSONB, nullable=False)
    policy_version = Column(String, nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("transaction_id"),
        CheckConstraint(
            "status IN ('approved', 'two_factor_required', 'rejected')",
            name="ck_pix_analyses_status",
        ),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_pix_analyses_metadata_object"),
        CheckConstraint(
            "jsonb_typeof(evidence_snapshot) = 'object'",
            name="ck_pix_analyses_evidence_object",
        ),
    )

    transaction = relationship("PixTransaction", back_populates="analysis")
