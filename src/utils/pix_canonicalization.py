import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal


CANONICALIZATION_VERSION = "pix-official-v1"
_FRACTIONAL_SECONDS = re.compile(r"T\d{2}:\d{2}:\d{2}\.(\d+)")


def parse_aware_datetime(raw_value: str) -> datetime:
    if not isinstance(raw_value, str):
        raise ValueError("timestamp must be a string")

    precision = _FRACTIONAL_SECONDS.search(raw_value)
    if precision and len(precision.group(1)) > 6:
        raise ValueError("timestamp precision exceeds microseconds")

    parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _canonical_instant(raw_value: str) -> str:
    return parse_aware_datetime(raw_value).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _canonical_number(value: int | float) -> str:
    normalized = Decimal(str(value)).normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def canonicalize_pix_payload(payload: dict) -> dict:
    """Build the versioned business identity without changing the input payload.

    The current JSON contract makes a missing geolocation and an explicit null
    equivalent: both mean that no coordinates were supplied.
    """
    source_account = payload["source_account"]
    destination_account = payload["destination_account"]
    source = payload["source"]

    canonical_source = {
        "device_id": source["device_id"],
        "channel": source["channel"],
        "ip": source["ip"],
    }
    geolocation = source.get("geolocation")
    if geolocation is not None:
        canonical_source["geolocation"] = {
            "latitude": _canonical_number(geolocation["latitude"]),
            "longitude": _canonical_number(geolocation["longitude"]),
        }

    return {
        "id": payload["id"],
        "amount": payload["amount"],
        "transaction_date": _canonical_instant(payload["transaction_date"]),
        "source_account": {
            "branch": source_account["branch"],
            "account_number": source_account["account_number"],
            "opening_date": _canonical_instant(source_account["opening_date"]),
            "owner": {
                "document_number": source_account["owner"]["document_number"],
            },
        },
        "destination_account": {
            "participant": destination_account["participant"],
            "branch": destination_account["branch"],
            "account_number": destination_account["account_number"],
            "opening_date": _canonical_instant(destination_account["opening_date"]),
            "owner": {
                "document_number": destination_account["owner"]["document_number"],
            },
        },
        "source": canonical_source,
    }


def pix_request_fingerprint(payload: dict) -> str:
    serialized = json.dumps(
        canonicalize_pix_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
