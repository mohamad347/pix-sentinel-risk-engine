import math
from datetime import datetime


CONTEXTUAL_EVIDENCE_SCHEMA_VERSION = "pix-context-v2-candidate-v1"
EARTH_RADIUS_KM = 6371.0088


def classify_party_type(document: str) -> str:
    if not isinstance(document, str):
        return "unknown"
    normalized = document
    for separator in (".", "-", "/", " "):
        normalized = normalized.replace(separator, "")
    if len(normalized) == 11 and normalized.isdigit():
        return "pf"
    if len(normalized) == 14 and normalized.isalnum():
        return "pj"
    return "unknown"


def build_contextual_evidence(
    *,
    current_payload: dict,
    admitted_at: datetime,
    prior_attempt_count: int,
    prior_device_attempt_count: int,
    prior_amount_median_cents: int | None,
    prior_history_span_seconds: int,
    distinct_counterparties: dict[int, int],
    prior_geolocation: dict | None,
) -> dict:
    if prior_attempt_count == 0:
        device_observation = "insufficient_history"
        profile_observation = "insufficient_history"
    else:
        device_observation = "observed" if prior_device_attempt_count > 0 else "newly_observed"
        profile_observation = (
            "observed"
            if isinstance(prior_amount_median_cents, int) and prior_amount_median_cents > 0
            else "insufficient_history"
        )

    source = current_payload["source"]
    source_owner = current_payload["source_account"]["owner"]
    destination_owner = current_payload["destination_account"]["owner"]
    return {
        "schema_version": CONTEXTUAL_EVIDENCE_SCHEMA_VERSION,
        "device": {
            "observation": device_observation,
            "prior_attempt_count": int(prior_device_attempt_count),
        },
        "profile": {
            "observation": profile_observation,
            "prior_attempt_count": int(prior_attempt_count),
            "history_span_seconds": int(prior_history_span_seconds),
            "median_amount_cents": (
                int(prior_amount_median_cents)
                if prior_amount_median_cents is not None
                else None
            ),
        },
        "diversity": {
            f"{minutes}m": {"distinct_counterparty_count": int(count)}
            for minutes, count in distinct_counterparties.items()
        },
        "geolocation": _geolocation_evidence(
            source.get("geolocation"),
            admitted_at,
            prior_geolocation,
        ),
        "source_availability": {
            "channel": "observed" if source.get("channel") else "missing",
            "ip": "observed" if source.get("ip") else "missing",
        },
        "party_types": {
            "payer": classify_party_type(source_owner.get("document_number")),
            "payee": classify_party_type(destination_owner.get("document_number")),
        },
    }


def _geolocation_evidence(
    current_geolocation: dict | None,
    admitted_at: datetime,
    prior_geolocation: dict | None,
) -> dict:
    if current_geolocation is None:
        return {"observation": "missing"}
    if prior_geolocation is None:
        return {"observation": "insufficient_history"}
    try:
        current_latitude = _coordinate(current_geolocation["latitude"], -90, 90)
        current_longitude = _coordinate(current_geolocation["longitude"], -180, 180)
        prior_latitude = _coordinate(prior_geolocation["latitude"], -90, 90)
        prior_longitude = _coordinate(prior_geolocation["longitude"], -180, 180)
        prior_received_at = prior_geolocation["received_at"]
        if not isinstance(prior_received_at, datetime):
            raise ValueError
        elapsed_seconds = int((admitted_at - prior_received_at).total_seconds())
        if elapsed_seconds < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"observation": "invalid"}

    return {
        "observation": "observed",
        "distance_km": round(
            _haversine_km(
                current_latitude,
                current_longitude,
                prior_latitude,
                prior_longitude,
            ),
            3,
        ),
        "elapsed_seconds": elapsed_seconds,
    }


def _coordinate(value, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError
    return value


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    latitude_delta = math.radians(lat2 - lat1)
    longitude_delta = math.radians(lon2 - lon1)
    a = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))
