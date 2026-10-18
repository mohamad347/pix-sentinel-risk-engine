from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, INET, JSONB
from sqlalchemy.orm import relationship

from models.base import Base


class PixTransaction(Base):
    __tablename__ = "pix_transactions"

    id = Column(BigInteger, primary_key=True)
    external_id = Column(String(128), nullable=False)
    request_fingerprint = Column(CHAR(64), nullable=False)
    canonicalization_version = Column(String(32), nullable=False)
    request_payload = Column(JSONB, nullable=False)
    amount_cents = Column(BigInteger, nullable=False)
    transaction_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    payer_account_id = Column(
        BigInteger,
        ForeignKey(
            "accounts.id",
            name="fk_pix_transactions_payer_account",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    payee_account_id = Column(
        BigInteger,
        ForeignKey(
            "accounts.id",
            name="fk_pix_transactions_payee_account",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    device_id = Column(
        BigInteger,
        ForeignKey(
            "devices.id",
            name="fk_pix_transactions_device",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    source_channel = Column(String(32), nullable=False)
    source_ip = Column(INET, nullable=False)
    source_latitude = Column(DOUBLE_PRECISION, nullable=True)
    source_longitude = Column(DOUBLE_PRECISION, nullable=True)

    __table_args__ = (
        UniqueConstraint("external_id", name="pix_transactions_external_id_key"),
        CheckConstraint("amount_cents > 0", name="ck_pix_transactions_positive_amount"),
        CheckConstraint(
            "jsonb_typeof(request_payload) = 'object'",
            name="ck_pix_transactions_payload_object",
        ),
        CheckConstraint(
            "btrim(source_channel) <> ''",
            name="ck_pix_transactions_channel_not_blank",
        ),
        CheckConstraint(
            "(source_latitude IS NULL) = (source_longitude IS NULL)",
            name="ck_pix_transactions_geolocation_pair",
        ),
        CheckConstraint(
            "source_latitude IS NULL OR source_latitude BETWEEN -90 AND 90",
            name="ck_pix_transactions_latitude_range",
        ),
        CheckConstraint(
            "source_longitude IS NULL OR source_longitude BETWEEN -180 AND 180",
            name="ck_pix_transactions_longitude_range",
        ),
        Index(
            "ix_pix_transactions_payer_received_at",
            "payer_account_id",
            "received_at",
        ),
        Index(
            "ix_pix_transactions_payee_received_at",
            "payee_account_id",
            "received_at",
        ),
        Index(
            "ix_pix_transactions_device_received_at",
            "device_id",
            "received_at",
        ),
        Index(
            "ix_pix_transactions_source_ip_received_at",
            "source_ip",
            "received_at",
        ),
    )

    analysis = relationship("PixAnalysis", back_populates="transaction", uselist=False)
    payer_account = relationship(
        "Account",
        foreign_keys=[payer_account_id],
        back_populates="outgoing_transactions",
    )
    payee_account = relationship(
        "Account",
        foreign_keys=[payee_account_id],
        back_populates="incoming_transactions",
    )
    device = relationship("Device", back_populates="transactions")
