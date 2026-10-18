import math
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from constants import APP_ENV
from controllers.base_controller import BaseController
from controllers.pix_bootstrap_policy import PixBootstrapPolicy
from dtos import PixAnalysisDTO
from errors import InvalidPixPayload, PixAnalysisNotFound, PixTransactionConflict
from repositories import PixTransactionRepository
from utils.pix_canonicalization import (
    CANONICALIZATION_VERSION,
    parse_aware_datetime,
    pix_request_fingerprint,
)


EXTERNAL_ID_UNIQUE_CONSTRAINT = "pix_transactions_external_id_key"


def _parse_aware_datetime(raw_value: str) -> datetime:
    try:
        return parse_aware_datetime(raw_value)
    except (TypeError, ValueError):
        raise InvalidPixPayload()


def validate_and_extract_pix_data(payload: dict) -> dict:
    external_id = payload["id"]
    if external_id != external_id.strip() or "/" in external_id:
        raise InvalidPixPayload()

    amount = payload["amount"]
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or amount > 9223372036854775807:
        raise InvalidPixPayload()

    transaction_at = _parse_aware_datetime(payload["transaction_date"])
    _parse_aware_datetime(payload["source_account"]["opening_date"])
    _parse_aware_datetime(payload["destination_account"]["opening_date"])

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

    source_account = payload["source_account"]
    destination_account = payload["destination_account"]

    return {
        "external_id": external_id,
        "request_fingerprint": pix_request_fingerprint(payload),
        "canonicalization_version": CANONICALIZATION_VERSION,
        "request_payload": deepcopy(payload),
        "amount_cents": amount,
        "transaction_at": transaction_at,
        # Phase one records when the controller admits a valid new request.
        # The post-lock semantics for velocity remain a phase-three decision.
        "received_at": datetime.now(timezone.utc),
        "payer_document": source_account["owner"]["document_number"],
        "payer_branch": source_account["branch"],
        "payer_account_number": source_account["account_number"],
        "payee_participant": destination_account["participant"],
        "payee_branch": destination_account["branch"],
        "payee_account_number": destination_account["account_number"],
        "payee_document": destination_account["owner"]["document_number"],
        "source_device_id": payload["source"]["device_id"],
    }


class PixTransactionController(BaseController):
    def __init__(self) -> None:
        super().__init__(__name__)
        self.pix_transaction_repository = PixTransactionRepository(self.context)
        self.policy = PixBootstrapPolicy(APP_ENV)

    def create(self, payload: dict) -> dict:
        transaction_data = validate_and_extract_pix_data(payload)

        existing = self.pix_transaction_repository.get_transaction_by_external_id(
            transaction_data["external_id"]
        )
        if existing is not None:
            return self._resolve_existing(existing, transaction_data["request_fingerprint"])

        try:
            transaction = self.pix_transaction_repository.create_transaction(**transaction_data)
            decision = self.policy.evaluate(transaction_data)
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
