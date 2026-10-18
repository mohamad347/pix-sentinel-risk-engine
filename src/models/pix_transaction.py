from sqlalchemy import BigInteger, CHAR, CheckConstraint, Column, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from models.base import Base


class PixTransaction(Base):
    __tablename__ = "pix_transactions"

    id = Column(BigInteger, primary_key=True)
    external_id = Column(String, nullable=False)
    request_fingerprint = Column(CHAR(64), nullable=False)
    canonicalization_version = Column(String, nullable=False)
    request_payload = Column(JSONB, nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    transaction_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    payer_document = Column(String, nullable=False)
    payer_branch = Column(String, nullable=False)
    payer_account_number = Column(String, nullable=False)
    payee_participant = Column(String, nullable=False)
    payee_branch = Column(String, nullable=False)
    payee_account_number = Column(String, nullable=False)
    payee_document = Column(String, nullable=False)
    source_device_id = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("external_id", name="pix_transactions_external_id_key"),
        CheckConstraint("amount_cents > 0", name="ck_pix_transactions_positive_amount"),
    )

    analysis = relationship("PixAnalysis", back_populates="transaction", uselist=False)
