from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, func, text
from sqlalchemy.orm import contains_eager

from database import Context
from models import PixAnalysis, PixTransaction
from utils.pix_history import payer_advisory_lock_key


PAYER_LOCK_TIMEOUT_MILLISECONDS = 1000


class PixTransactionRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create_transaction(self, **transaction_data) -> PixTransaction:
        transaction = PixTransaction(**transaction_data)
        self.session.add(transaction)
        self.session.flush()
        return transaction

    def acquire_payer_lock(self, document: str, branch: str, account_number: str) -> None:
        lock_key = payer_advisory_lock_key(document, branch, account_number)
        self.session.execute(
            text(f"SET LOCAL lock_timeout = '{PAYER_LOCK_TIMEOUT_MILLISECONDS}ms'")
        )
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    def get_database_clock(self) -> datetime:
        return self.session.execute(text("SELECT clock_timestamp()")).scalar_one()

    def get_payer_history(
        self,
        transaction: PixTransaction,
        windows_minutes: tuple[int, ...],
    ) -> dict:
        is_prior_transaction = PixTransaction.id != transaction.id
        is_same_counterparty = and_(
            PixTransaction.payee_participant == transaction.payee_participant,
            PixTransaction.payee_branch == transaction.payee_branch,
            PixTransaction.payee_account_number == transaction.payee_account_number,
            PixTransaction.payee_document == transaction.payee_document,
        )
        columns = [
            func.count(PixTransaction.id)
            .filter(is_prior_transaction)
            .label("prior_attempt_count"),
            func.count(PixTransaction.id)
            .filter(is_prior_transaction, is_same_counterparty)
            .label("prior_counterparty_attempt_count"),
        ]
        for minutes in windows_minutes:
            in_window = PixTransaction.received_at > transaction.received_at - timedelta(
                minutes=minutes
            )
            columns.extend(
                [
                    func.count(PixTransaction.id)
                    .filter(in_window)
                    .label(f"attempt_count_{minutes}m"),
                    func.coalesce(
                        func.sum(PixTransaction.amount_cents).filter(in_window),
                        0,
                    ).label(f"amount_cents_{minutes}m"),
                ]
            )

        row = (
            self.session.query(*columns)
            .filter(
                PixTransaction.payer_document == transaction.payer_document,
                PixTransaction.payer_branch == transaction.payer_branch,
                PixTransaction.payer_account_number == transaction.payer_account_number,
                PixTransaction.received_at <= transaction.received_at,
            )
            .one()
        )
        values = row._mapping
        return {
            "prior_attempt_count": int(values["prior_attempt_count"]),
            "prior_counterparty_attempt_count": int(
                values["prior_counterparty_attempt_count"]
            ),
            "windows": {
                minutes: {
                    "attempt_count": int(values[f"attempt_count_{minutes}m"]),
                    "amount_cents": int(values[f"amount_cents_{minutes}m"]),
                }
                for minutes in windows_minutes
            },
        }

    def create_analysis(
        self,
        transaction: PixTransaction,
        status: str,
        decision_metadata: dict,
        evidence_snapshot: dict,
        policy_version: str,
    ) -> PixAnalysis:
        analysis = PixAnalysis(
            analysis_key=uuid4(),
            transaction_id=transaction.id,
            status=status,
            decision_metadata=decision_metadata,
            evidence_snapshot=evidence_snapshot,
            policy_version=policy_version,
            evaluated_at=datetime.now(timezone.utc),
        )
        self.session.add(analysis)
        self.session.flush()
        return analysis

    def get_analysis_by_external_id(self, external_id: str) -> PixAnalysis:
        return (
            self.session.query(PixAnalysis)
            .join(PixTransaction, PixAnalysis.transaction_id == PixTransaction.id)
            .filter(PixTransaction.external_id == external_id)
            .one_or_none()
        )

    def get_transaction_by_external_id(self, external_id: str) -> PixTransaction:
        return (
            self.session.query(PixTransaction)
            .join(PixAnalysis, PixAnalysis.transaction_id == PixTransaction.id)
            .options(contains_eager(PixTransaction.analysis))
            .filter(PixTransaction.external_id == external_id)
            .one_or_none()
        )
