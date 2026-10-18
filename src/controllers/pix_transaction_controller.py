import math
from copy import deepcopy
from datetime import datetime
from ipaddress import ip_address

from sqlalchemy.exc import IntegrityError

from controllers.base_controller import BaseController
from controllers.pix_policy_selector import build_risk_policy
from dtos import PixAnalysisDTO
from errors import InvalidPixPayload, PixAnalysisNotFound, PixTransactionConflict
from repositories import PixTransactionRepository
from utils.pix_canonicalization import (
    CANONICALIZATION_VERSION,
    parse_aware_datetime,
    pix_request_fingerprint,
)
from utils.pix_context import (
    build_adaptive_contextual_evidence,
    build_contextual_evidence,
    normalize_document,
)
from utils.pix_history import HISTORY_WINDOWS_MINUTES, build_evidence_snapshot


EXTERNAL_ID_UNIQUE_CONSTRAINT = "pix_transactions_external_id_key"


def _parse_aware_datetime(raw_value: str) -> datetime:
    try:
        return parse_aware_datetime(raw_value)
    except (TypeError, ValueError):
        raise InvalidPixPayload()


def _require_identifier(value: str, maximum_length: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum_length
    ):
        raise InvalidPixPayload()
    return value


def validate_and_extract_pix_data(payload: dict) -> dict:
    external_id = _require_identifier(payload["id"], 128)
    if "/" in external_id:
        raise InvalidPixPayload()

    amount = payload["amount"]
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or amount > 9223372036854775807:
        raise InvalidPixPayload()

    transaction_at = _parse_aware_datetime(payload["transaction_date"])
    source_account = payload["source_account"]
    destination_account = payload["destination_account"]
    payer_branch = _require_identifier(source_account["branch"], 64)
    payer_account_number = _require_identifier(
        source_account["account_number"], 64
    )
    payee_participant = _require_identifier(
        destination_account["participant"], 64
    )
    payee_branch = _require_identifier(destination_account["branch"], 64)
    payee_account_number = _require_identifier(
        destination_account["account_number"], 64
    )
    device_identifier = _require_identifier(payload["source"]["device_id"], 128)
    source_channel = _require_identifier(payload["source"]["channel"], 32)
    source_opening_at = _parse_aware_datetime(source_account["opening_date"])
    destination_opening_at = _parse_aware_datetime(
        destination_account["opening_date"]
    )

    payer_document = normalize_document(source_account["owner"]["document_number"])
    payee_document = normalize_document(
        destination_account["owner"]["document_number"]
    )
    if not payer_document or not payee_document:
        raise InvalidPixPayload()

    try:
        source_ip = str(ip_address(payload["source"]["ip"]))
    except (TypeError, ValueError):
        raise InvalidPixPayload()

    geolocation = payload["source"].get("geolocation")
    if geolocation is not None:
        latitude = geolocation["latitude"]
        longitude = geolocation["longitude"]
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or not isinstance(longitude, (int, float))
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
        ):
            raise InvalidPixPayload()

    source_latitude = None
    source_longitude = None
    if geolocation is not None:
        source_latitude = float(geolocation["latitude"])
        source_longitude = float(geolocation["longitude"])

    return {
        "external_id": external_id,
        "request_fingerprint": pix_request_fingerprint(payload),
        "canonicalization_version": CANONICALIZATION_VERSION,
        "request_payload": deepcopy(payload),
        "amount_cents": amount,
        "transaction_at": transaction_at,
        "payer_account_data": {
            "participant": None,
            "branch": payer_branch,
            "account_number": payer_account_number,
            "owner_document": payer_document,
            "opening_at": source_opening_at,
        },
        "payee_account_data": {
            "participant": payee_participant,
            "branch": payee_branch,
            "account_number": payee_account_number,
            "owner_document": payee_document,
            "opening_at": destination_opening_at,
        },
        "device_identifier": device_identifier,
        "source_channel": source_channel,
        "source_ip": source_ip,
        "source_latitude": source_latitude,
        "source_longitude": source_longitude,
    }


class PixTransactionController(BaseController):
    def __init__(self) -> None:
        super().__init__(__name__)
        self.pix_transaction_repository = PixTransactionRepository(self.context)
        self.policy = build_risk_policy()

    def create(self, payload: dict) -> dict:
        transaction_data = validate_and_extract_pix_data(payload)

        existing = self.pix_transaction_repository.get_transaction_by_external_id(
            transaction_data["external_id"]
        )
        if existing is not None:
            return self._resolve_existing(existing, transaction_data["request_fingerprint"])

        try:
            self.pix_transaction_repository.acquire_payer_lock(
                transaction_data["payer_account_data"]["owner_document"],
                transaction_data["payer_account_data"]["branch"],
                transaction_data["payer_account_data"]["account_number"],
            )

            existing = self.pix_transaction_repository.get_transaction_by_external_id(
                transaction_data["external_id"]
            )
            if existing is not None:
                return self._resolve_existing(existing, transaction_data["request_fingerprint"])

            transaction_data["received_at"] = self.pix_transaction_repository.get_database_clock()
            transaction = self.pix_transaction_repository.create_transaction(**transaction_data)
            requires_context = getattr(
                self.policy,
                "requires_contextual_evidence",
                False,
            )
            requires_adaptive_context = getattr(
                self.policy,
                "requires_adaptive_contextual_evidence",
                False,
            )
            history_windows = getattr(
                self.policy,
                "history_windows_minutes",
                HISTORY_WINDOWS_MINUTES,
            )
            if requires_context:
                history = self.pix_transaction_repository.get_payer_history(
                    transaction,
                    history_windows,
                    include_contextual=True,
                    profile_approved_only=getattr(
                        self.policy,
                        "requires_approved_profile_history",
                        False,
                    ),
                    include_adaptive=requires_adaptive_context,
                )
                if requires_adaptive_context:
                    contextual_evidence = build_adaptive_contextual_evidence(
                        current_payload=transaction.request_payload,
                        admitted_at=transaction.received_at,
                        prior_attempt_count=history["prior_attempt_count"],
                        prior_counterparty_attempt_count=history[
                            "prior_counterparty_attempt_count"
                        ],
                        prior_device_attempt_count=history[
                            "prior_device_attempt_count"
                        ],
                        prior_ip_attempt_count=history["prior_ip_attempt_count"],
                        prior_channel_attempt_count=history[
                            "prior_channel_attempt_count"
                        ],
                        prior_amount_median_cents=history[
                            "prior_amount_median_cents"
                        ],
                        prior_amount_p90_cents=history["prior_amount_p90_cents"],
                        prior_history_span_seconds=history[
                            "prior_history_span_seconds"
                        ],
                        profile_attempt_count=history["prior_profile_attempt_count"],
                        prior_nighttime_attempt_count=history[
                            "prior_nighttime_attempt_count"
                        ],
                        pair_attempt_count_15m=history["pair_attempt_counts"][15],
                        distinct_counterparties=history["distinct_counterparties"],
                        distinct_devices=history["distinct_devices"],
                        distinct_ips=history["distinct_ips"],
                        prior_geolocation=history["prior_geolocation"],
                        beneficiary_context=history["beneficiary_context"],
                        access_network_context=history[
                            "access_network_context"
                        ],
                    )
                else:
                    contextual_evidence = build_contextual_evidence(
                        current_payload=transaction.request_payload,
                        admitted_at=transaction.received_at,
                        prior_attempt_count=history["prior_attempt_count"],
                        prior_device_attempt_count=history[
                            "prior_device_attempt_count"
                        ],
                        prior_amount_median_cents=history[
                            "prior_amount_median_cents"
                        ],
                        prior_history_span_seconds=history[
                            "prior_history_span_seconds"
                        ],
                        distinct_counterparties=history[
                            "distinct_counterparties"
                        ],
                        prior_geolocation=history["prior_geolocation"],
                        profile_attempt_count=history[
                            "prior_profile_attempt_count"
                        ],
                    )
            else:
                history = self.pix_transaction_repository.get_payer_history(
                    transaction,
                    history_windows,
                )
                contextual_evidence = None
            evidence_snapshot = build_evidence_snapshot(
                admitted_at=transaction.received_at,
                prior_attempt_count=history["prior_attempt_count"],
                prior_counterparty_attempt_count=history[
                    "prior_counterparty_attempt_count"
                ],
                windows=history["windows"],
                contextual=contextual_evidence,
            )
            decision = self.policy.evaluate(transaction_data, evidence_snapshot)
            analysis = self.pix_transaction_repository.create_analysis(
                transaction,
                status=decision["status"],
                decision_metadata=decision["metadata"],
                evidence_snapshot=decision["evidence_snapshot"],
                policy_version=decision["policy_version"],
            )
            response = PixAnalysisDTO.obj_to_dict(analysis)
            self.session.commit()
        except IntegrityError as exception:
            self.session.rollback()
            if self._constraint_name(exception) != EXTERNAL_ID_UNIQUE_CONSTRAINT:
                raise

            winner = self.pix_transaction_repository.get_transaction_by_external_id(
                transaction_data["external_id"]
            )
            if winner is None:
                raise
            return self._resolve_existing(winner, transaction_data["request_fingerprint"])
        except Exception:
            self.session.rollback()
            raise

        return response

    @staticmethod
    def _constraint_name(exception: IntegrityError) -> str | None:
        diagnostic = getattr(exception.orig, "diag", None)
        return getattr(diagnostic, "constraint_name", None)

    @staticmethod
    def _resolve_existing(existing, request_fingerprint: str) -> dict:
        if getattr(existing, "canonicalization_version", None) == CANONICALIZATION_VERSION:
            persisted_fingerprint = existing.request_fingerprint
        else:
            # Phase-one hashes did not normalize instants. Recomputing from the
            # preserved original payload avoids false conflicts for those rows.
            persisted_fingerprint = pix_request_fingerprint(existing.request_payload)
        if persisted_fingerprint != request_fingerprint:
            raise PixTransactionConflict()
        return PixAnalysisDTO.obj_to_dict(existing.analysis)

    def get_by_external_id(self, external_id: str) -> dict:
        analysis = self.pix_transaction_repository.get_analysis_by_external_id(external_id)
        if analysis is None:
            raise PixAnalysisNotFound()
        return PixAnalysisDTO.obj_to_dict(analysis)
