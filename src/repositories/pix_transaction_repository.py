from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, case, func, text, tuple_
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
        include_contextual: bool = False,
        profile_approved_only: bool = False,
        include_adaptive: bool = False,
    ) -> dict:
        is_prior_transaction = PixTransaction.id != transaction.id
        is_profile_eligible = is_prior_transaction
        if profile_approved_only:
            is_profile_eligible = and_(
                is_prior_transaction,
                PixAnalysis.status == "approved",
            )
        is_same_counterparty = and_(
            PixTransaction.payee_participant == transaction.payee_participant,
            PixTransaction.payee_branch == transaction.payee_branch,
            PixTransaction.payee_account_number == transaction.payee_account_number,
            PixTransaction.payee_document == transaction.payee_document,
        )
        is_same_device = PixTransaction.source_device_id == transaction.source_device_id
        current_source = transaction.request_payload.get("source", {})
        stored_source = PixTransaction.request_payload["source"]
        is_same_ip = stored_source["ip"].astext == current_source.get("ip")
        is_same_channel = stored_source["channel"].astext == current_source.get("channel")
        columns = [
            func.count(PixTransaction.id)
            .filter(is_prior_transaction)
            .label("prior_attempt_count"),
            func.count(PixTransaction.id)
            .filter(is_prior_transaction, is_same_counterparty)
            .label("prior_counterparty_attempt_count"),
        ]
        if include_contextual:
            columns.extend(
                [
                    func.count(PixTransaction.id)
                    .filter(is_prior_transaction, is_same_device)
                    .label("prior_device_attempt_count"),
                    func.percentile_disc(0.5)
                    .within_group(
                        case(
                            (is_profile_eligible, PixTransaction.amount_cents),
                            else_=None,
                        )
                    )
                    .label("prior_amount_median_cents"),
                    func.count(PixTransaction.id)
                    .filter(is_profile_eligible)
                    .label("prior_profile_attempt_count"),
                    func.min(PixTransaction.received_at)
                    .filter(is_profile_eligible)
                    .label("prior_first_received_at"),
                    func.max(PixTransaction.received_at)
                    .filter(is_profile_eligible)
                    .label("prior_last_received_at"),
                ]
            )
        if include_adaptive:
            profile_amount = case(
                (is_profile_eligible, PixTransaction.amount_cents),
                else_=None,
            )
            utc_hour = func.extract(
                "hour",
                func.timezone("UTC", PixTransaction.transaction_at),
            )
            columns.extend(
                [
                    func.count(PixTransaction.id)
                    .filter(is_prior_transaction, is_same_ip)
                    .label("prior_ip_attempt_count"),
                    func.count(PixTransaction.id)
                    .filter(is_prior_transaction, is_same_channel)
                    .label("prior_channel_attempt_count"),
                    func.percentile_disc(0.9)
                    .within_group(profile_amount)
                    .label("prior_amount_p90_cents"),
                    func.count(PixTransaction.id)
                    .filter(is_profile_eligible, utc_hour < 6)
                    .label("prior_nighttime_attempt_count"),
                ]
            )
        for minutes in windows_minutes:
            in_window = PixTransaction.received_at > transaction.received_at - timedelta(
                minutes=minutes
            )
            window_columns = [
                    func.count(PixTransaction.id)
                    .filter(in_window)
                    .label(f"attempt_count_{minutes}m"),
                    func.coalesce(
                        func.sum(PixTransaction.amount_cents).filter(in_window),
                        0,
                    ).label(f"amount_cents_{minutes}m"),
            ]
            if include_contextual:
                window_columns.append(
                    func.count(
                        func.distinct(
                            tuple_(
                                PixTransaction.payee_participant,
                                PixTransaction.payee_branch,
                                PixTransaction.payee_account_number,
                                PixTransaction.payee_document,
                            )
                        )
                    )
                    .filter(in_window)
                    .label(f"distinct_counterparties_{minutes}m"),
                )
            if include_adaptive:
                window_columns.extend(
                    [
                        func.count(PixTransaction.id)
                        .filter(in_window, is_same_counterparty)
                        .label(f"pair_attempt_count_{minutes}m"),
                        func.count(func.distinct(PixTransaction.source_device_id))
                        .filter(in_window)
                        .label(f"distinct_devices_{minutes}m"),
                        func.count(func.distinct(stored_source["ip"].astext))
                        .filter(in_window)
                        .label(f"distinct_ips_{minutes}m"),
                    ]
                )
            columns.extend(window_columns)

        query = self.session.query(*columns)
        if profile_approved_only:
            query = query.outerjoin(
                PixAnalysis,
                PixAnalysis.transaction_id == PixTransaction.id,
            )
        row = (
            query
            .filter(
                PixTransaction.payer_document == transaction.payer_document,
                PixTransaction.payer_branch == transaction.payer_branch,
                PixTransaction.payer_account_number == transaction.payer_account_number,
                PixTransaction.received_at <= transaction.received_at,
            )
            .one()
        )
        values = row._mapping
        result = {
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
        if include_contextual:
            first_received_at = values["prior_first_received_at"]
            last_received_at = values["prior_last_received_at"]
            result.update(
                {
                    "prior_device_attempt_count": int(
                        values["prior_device_attempt_count"]
                    ),
                    "prior_profile_attempt_count": int(
                        values["prior_profile_attempt_count"]
                    ),
                    "prior_amount_median_cents": (
                        int(values["prior_amount_median_cents"])
                        if values["prior_amount_median_cents"] is not None
                        else None
                    ),
                    "prior_history_span_seconds": (
                        int((last_received_at - first_received_at).total_seconds())
                        if first_received_at is not None and last_received_at is not None
                        else 0
                    ),
                    "prior_geolocation": self._get_latest_prior_geolocation(transaction),
                    "distinct_counterparties": {
                        minutes: int(values[f"distinct_counterparties_{minutes}m"])
                        for minutes in windows_minutes
                    },
                }
            )
        if include_adaptive:
            network_context = self.get_beneficiary_context(transaction)
            result.update(
                {
                    "prior_ip_attempt_count": int(values["prior_ip_attempt_count"]),
                    "prior_channel_attempt_count": int(
                        values["prior_channel_attempt_count"]
                    ),
                    "prior_amount_p90_cents": (
                        int(values["prior_amount_p90_cents"])
                        if values["prior_amount_p90_cents"] is not None
                        else None
                    ),
                    "prior_nighttime_attempt_count": int(
                        values["prior_nighttime_attempt_count"]
                    ),
                    "pair_attempt_counts": {
                        minutes: int(values[f"pair_attempt_count_{minutes}m"])
                        for minutes in windows_minutes
                    },
                    "distinct_devices": {
                        minutes: int(values[f"distinct_devices_{minutes}m"])
                        for minutes in windows_minutes
                    },
                    "distinct_ips": {
                        minutes: int(values[f"distinct_ips_{minutes}m"])
                        for minutes in windows_minutes
                    },
                    "beneficiary_context": network_context["beneficiary"],
                    "access_network_context": network_context["access"],
                }
            )
        return result

    def get_beneficiary_context(self, transaction: PixTransaction) -> dict:
        """Return prior-only, system-local recipient fan-in evidence."""
        is_prior = PixTransaction.id != transaction.id
        in_60m = PixTransaction.received_at > transaction.received_at - timedelta(
            minutes=60
        )
        same_beneficiary = and_(
            PixTransaction.payee_participant == transaction.payee_participant,
            PixTransaction.payee_branch == transaction.payee_branch,
            PixTransaction.payee_account_number == transaction.payee_account_number,
            PixTransaction.payee_document == transaction.payee_document,
        )
        stored_source = PixTransaction.request_payload["source"]
        current_source = transaction.request_payload.get("source", {})
        same_device = PixTransaction.source_device_id == transaction.source_device_id
        same_ip = stored_source["ip"].astext == current_source.get("ip")
        row = (
            self.session.query(
                func.count(PixTransaction.id)
                .filter(is_prior, same_beneficiary)
                .label("prior_attempt_count"),
                func.count(PixTransaction.id)
                .filter(is_prior, in_60m, same_beneficiary)
                .label("attempt_count_60m"),
                func.count(
                    func.distinct(
                        tuple_(
                            PixTransaction.payer_document,
                            PixTransaction.payer_branch,
                            PixTransaction.payer_account_number,
                        )
                    )
                )
                .filter(is_prior, in_60m, same_beneficiary)
                .label("distinct_payer_count_60m"),
                func.count(
                    func.distinct(
                        tuple_(
                            PixTransaction.payer_document,
                            PixTransaction.payer_branch,
                            PixTransaction.payer_account_number,
                        )
                    )
                )
                .filter(is_prior, in_60m, same_device)
                .label("device_distinct_payer_count_60m"),
                func.count(
                    func.distinct(
                        tuple_(
                            PixTransaction.payer_document,
                            PixTransaction.payer_branch,
                            PixTransaction.payer_account_number,
                        )
                    )
                )
                .filter(is_prior, in_60m, same_ip)
                .label("ip_distinct_payer_count_60m"),
            )
            .filter(PixTransaction.received_at <= transaction.received_at)
            .one()
        )
        values = row._mapping
        prior_attempt_count = int(values["prior_attempt_count"])
        return {
            "beneficiary": {
                "global_observation": (
                    "observed" if prior_attempt_count > 0 else "not_observed"
                ),
                "attempt_count_60m": int(values["attempt_count_60m"]),
                "distinct_payer_count_60m": int(
                    values["distinct_payer_count_60m"]
                ),
            },
            "access": {
                "device_distinct_payer_count_60m": int(
                    values["device_distinct_payer_count_60m"]
                ),
                "ip_distinct_payer_count_60m": int(
                    values["ip_distinct_payer_count_60m"]
                ),
            },
        }

    def _get_latest_prior_geolocation(self, transaction: PixTransaction) -> dict | None:
        if transaction.request_payload.get("source", {}).get("geolocation") is None:
            return None
        geolocation = PixTransaction.request_payload["source"]["geolocation"]
        row = (
            self.session.query(PixTransaction.request_payload, PixTransaction.received_at)
            .join(PixAnalysis, PixAnalysis.transaction_id == PixTransaction.id)
            .filter(
                PixTransaction.payer_document == transaction.payer_document,
                PixTransaction.payer_branch == transaction.payer_branch,
                PixTransaction.payer_account_number == transaction.payer_account_number,
                PixTransaction.id != transaction.id,
                PixTransaction.received_at <= transaction.received_at,
                PixAnalysis.status == "approved",
                func.jsonb_typeof(geolocation) == "object",
            )
            .order_by(PixTransaction.received_at.desc(), PixTransaction.id.desc())
            .limit(1)
            .one_or_none()
        )
        if row is None:
            return None
        coordinates = row.request_payload["source"]["geolocation"]
        return {
            "latitude": coordinates.get("latitude"),
            "longitude": coordinates.get("longitude"),
            "received_at": row.received_at,
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
