import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


CONTEXTUAL_EVIDENCE_SCHEMA_VERSION = "pix-context-v2-candidate-v1"
ADAPTIVE_CONTEXTUAL_EVIDENCE_SCHEMA_VERSION = "pix-context-v3-r4-v4"
EARTH_RADIUS_KM = 6371.0088
BRAZIL_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def normalize_document(document: str) -> str:
    if not isinstance(document, str):
        return ""
    normalized = document
    for separator in (".", "-", "/", " "):
        normalized = normalized.replace(separator, "")
    return normalized.upper()


def classify_party_type(document: str) -> str:
    normalized = normalize_document(document)
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
    profile_attempt_count: int | None = None,
) -> dict:
    if profile_attempt_count is None:
        profile_attempt_count = prior_attempt_count
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
            "prior_attempt_count": int(profile_attempt_count),
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


def build_adaptive_contextual_evidence(
    *,
    current_payload: dict,
    admitted_at: datetime,
    prior_attempt_count: int,
    prior_counterparty_attempt_count: int,
    prior_device_attempt_count: int,
    prior_ip_attempt_count: int,
    prior_channel_attempt_count: int,
    prior_amount_median_cents: int | None,
    prior_amount_p90_cents: int | None,
    prior_history_span_seconds: int,
    profile_attempt_count: int,
    prior_nighttime_attempt_count: int,
    pair_attempt_count_15m: int,
    distinct_counterparties: dict[int, int],
    distinct_devices: dict[int, int],
    distinct_ips: dict[int, int],
    prior_geolocation: dict | None,
    beneficiary_context: dict,
    access_network_context: dict,
) -> dict:
    """Build a privacy-minimized snapshot for the adaptive risk policy.

    Identifiers, IPs and coordinates are compared inside the transaction but are
    never copied into evidence. Only counts, states and derived distances remain.
    """
    source = current_payload["source"]
    payer_document = current_payload["source_account"]["owner"].get("document_number")
    payee_document = current_payload["destination_account"]["owner"].get(
        "document_number"
    )
    if prior_attempt_count == 0:
        device_observation = "insufficient_history"
        ip_observation = "insufficient_history" if source.get("ip") else "missing"
        channel_observation = (
            "insufficient_history" if source.get("channel") else "missing"
        )
    else:
        device_observation = (
            "observed" if prior_device_attempt_count > 0 else "newly_observed"
        )
        ip_observation = (
            "observed"
            if prior_ip_attempt_count > 0
            else "newly_observed"
            if source.get("ip")
            else "missing"
        )
        channel_observation = (
            "observed"
            if prior_channel_attempt_count > 0
            else "changed"
            if source.get("channel")
            else "missing"
        )

    profile_observation = (
        "observed"
        if profile_attempt_count > 0
        and isinstance(prior_amount_median_cents, int)
        and prior_amount_median_cents > 0
        and isinstance(prior_amount_p90_cents, int)
        and prior_amount_p90_cents > 0
        else "insufficient_history"
    )
    transaction_at = _payload_datetime(current_payload["transaction_date"])
    source_opened_at = _payload_datetime(current_payload["source_account"]["opening_date"])
    destination_opened_at = _payload_datetime(
        current_payload["destination_account"]["opening_date"]
    )
    clock_skew = int((admitted_at - transaction_at).total_seconds())
    destination_age_days = int(
        (admitted_at - destination_opened_at).total_seconds() // 86_400
    )
    source_age_days = int((admitted_at - source_opened_at).total_seconds() // 86_400)
    nighttime_observation = _nighttime_observation(
        transaction_at,
        profile_attempt_count,
        prior_nighttime_attempt_count,
    )

    return {
        "schema_version": ADAPTIVE_CONTEXTUAL_EVIDENCE_SCHEMA_VERSION,
        "profile": {
            "observation": profile_observation,
            "prior_attempt_count": int(profile_attempt_count),
            "history_span_seconds": int(prior_history_span_seconds),
            "median_amount_cents": (
                int(prior_amount_median_cents)
                if prior_amount_median_cents is not None
                else None
            ),
            "p90_amount_cents": (
                int(prior_amount_p90_cents)
                if prior_amount_p90_cents is not None
                else None
            ),
        },
        "relationship": {
            "prior_counterparty_attempt_count": int(prior_counterparty_attempt_count),
            "pair_attempt_count_15m": int(pair_attempt_count_15m),
            "distinct_counterparty_count_60m": int(
                distinct_counterparties.get(60, 0)
            ),
        },
        "access": {
            "device_observation": device_observation,
            "ip_observation": ip_observation,
            "channel_observation": channel_observation,
            "distinct_device_count_60m": int(distinct_devices.get(60, 0)),
            "distinct_ip_count_60m": int(distinct_ips.get(60, 0)),
            "device_distinct_payer_count_60m": int(
                access_network_context["device_distinct_payer_count_60m"]
            ),
            "ip_distinct_payer_count_60m": int(
                access_network_context["ip_distinct_payer_count_60m"]
            ),
        },
        "temporal": {
            "transaction_clock_skew_seconds": clock_skew,
            "nighttime_observation": nighttime_observation,
            "geolocation": _geolocation_evidence(
                source.get("geolocation"), admitted_at, prior_geolocation
            ),
        },
        "beneficiary": {
            "global_observation": beneficiary_context["global_observation"],
            "distinct_payer_count_60m": int(
                beneficiary_context["distinct_payer_count_60m"]
            ),
            "attempt_count_60m": int(beneficiary_context["attempt_count_60m"]),
            "destination_account_age_days": destination_age_days,
        },
        "account_context": {
            "source_account_age_days": source_age_days,
            "is_self_transfer": bool(
                normalize_document(payer_document)
                and normalize_document(payer_document) == normalize_document(payee_document)
            ),
        },
        "party_types": {
            "payer": classify_party_type(payer_document),
            "payee": classify_party_type(payee_document),
        },
    }


def _payload_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return result


def _nighttime_observation(
    transaction_at: datetime,
    profile_attempt_count: int,
    prior_nighttime_attempt_count: int,
) -> str:
    brazil_hour = transaction_at.astimezone(BRAZIL_TIMEZONE).hour
    if not (brazil_hour >= 20 or brazil_hour < 6):
        return "not_nighttime"
    if profile_attempt_count < 5:
        return "insufficient_history"
    if prior_nighttime_attempt_count * 5 <= profile_attempt_count:
        return "unusual"
    return "habitual"


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

    distance_km = round(
        _haversine_km(
            current_latitude,
            current_longitude,
            prior_latitude,
            prior_longitude,
        ),
        3,
    )
    return {
        "observation": "observed",
        "distance_km": distance_km,
        "elapsed_seconds": elapsed_seconds,
        "speed_kmh": (
            round(distance_km * 3_600 / elapsed_seconds, 3)
            if elapsed_seconds > 0
            else None
        ),
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
