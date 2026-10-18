import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone

from constants import APP_ENV
from controllers.base_controller import BaseController
from controllers.pix_bootstrap_policy import PixBootstrapPolicy
from dtos import PixAnalysisDTO
from errors import InvalidPixPayload, PixAnalysisNotFound
from repositories import PixTransactionRepository


def _parse_aware_datetime(raw_value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise InvalidPixPayload()

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidPixPayload()

    return parsed


def _official_payload(payload: dict) -> dict:
    """Select only challenge fields for the provisional phase-one hash."""
    source = payload["source"]
    official_source = {
        "device_id": source["device_id"],
        "channel": source["channel"],
        "ip": source["ip"],
    }
    if "geolocation" in source:
        official_source["geolocation"] = source["geolocation"]

    return {
        "id": payload["id"],
        "amount": payload["amount"],
        "transaction_date": payload["transaction_date"],
        "source_account": deepcopy(payload["source_account"]),
        "destination_account": deepcopy(payload["destination_account"]),
        "source": deepcopy(official_source),
    }


def _provisional_fingerprint(payload: dict) -> str:
    """Fill the structural column; replay canonization belongs to phase two."""
    serialized = json.dumps(_official_payload(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
        "request_fingerprint": _provisional_fingerprint(payload),
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
        decision = self.policy.evaluate(transaction_data)

        try:
            transaction = self.pix_transaction_repository.create_transaction(**transaction_data)
            analysis = self.pix_transaction_repository.create_analysis(
                transaction,
                status=decision["status"],
                decision_metadata=decision["metadata"],
                evidence_snapshot=decision["evidence_snapshot"],
                policy_version=decision["policy_version"],
            )
            response = PixAnalysisDTO.obj_to_dict(analysis)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return response

    def get_by_external_id(self, external_id: str) -> dict:
        analysis = self.pix_transaction_repository.get_analysis_by_external_id(external_id)
        if analysis is None:
            raise PixAnalysisNotFound()
        return PixAnalysisDTO.obj_to_dict(analysis)
