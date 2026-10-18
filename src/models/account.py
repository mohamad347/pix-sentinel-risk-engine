from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, String, UniqueConstraint
from sqlalchemy.orm import relationship

from models.base import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(BigInteger, primary_key=True)
    participant = Column(String(64), nullable=True)
    branch = Column(String(64), nullable=False)
    account_number = Column(String(64), nullable=False)
    owner_document = Column(String(64), nullable=False)
    opening_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "participant",
            "branch",
            "account_number",
            "owner_document",
            name="accounts_identity_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "participant IS NULL OR btrim(participant) <> ''",
            name="ck_accounts_participant_not_blank",
        ),
        CheckConstraint("btrim(branch) <> ''", name="ck_accounts_branch_not_blank"),
        CheckConstraint(
            "btrim(account_number) <> ''",
            name="ck_accounts_number_not_blank",
        ),
        CheckConstraint(
            "btrim(owner_document) <> ''",
            name="ck_accounts_document_not_blank",
        ),
    )

    outgoing_transactions = relationship(
        "PixTransaction",
        foreign_keys="PixTransaction.payer_account_id",
        back_populates="payer_account",
    )
    incoming_transactions = relationship(
        "PixTransaction",
        foreign_keys="PixTransaction.payee_account_id",
        back_populates="payee_account",
    )
