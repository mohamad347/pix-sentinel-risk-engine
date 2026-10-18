from datetime import datetime, timezone
from uuid import uuid4

from database import Context
from models import PixAnalysis, PixTransaction


class PixTransactionRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def create_transaction(self, **transaction_data) -> PixTransaction:
        transaction = PixTransaction(**transaction_data)
        self.session.add(transaction)
        self.session.flush()
        return transaction

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
