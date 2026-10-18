from datetime import datetime, timedelta, timezone

from utils.pix_context import build_contextual_evidence, classify_party_type


NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)


def payload(*, document="12345678901", device="device-new", geolocation=None):
    source = {"device_id": device, "channel": "app", "ip": "198.51.100.23"}
    if geolocation is not None:
        source["geolocation"] = geolocation
    return {
        "source_account": {"owner": {"document_number": document}},
        "destination_account": {"owner": {"document_number": "00ABC123450001"}},
        "source": source,
    }


def test_builds_context_without_pii_and_keeps_missing_geo_neutral():
    context = build_contextual_evidence(
        current_payload=payload(),
        admitted_at=NOW,
        prior_attempt_count=0,
        prior_device_attempt_count=0,
        prior_amount_median_cents=None,
        prior_history_span_seconds=0,
        distinct_counterparties={5: 1, 60: 1},
        prior_geolocation=None,
    )

    assert context["device"] == {
        "observation": "insufficient_history",
        "prior_attempt_count": 0,
    }
    assert context["profile"]["observation"] == "insufficient_history"
    assert context["geolocation"] == {"observation": "missing"}
    assert context["party_types"] == {"payer": "pf", "payee": "pj"}
    serialized = str(context)
    assert "12345678901" not in serialized
    assert "device-new" not in serialized
    assert "198.51.100.23" not in serialized


def test_observed_profile_device_diversity_and_derived_geo_have_no_raw_coordinates():
    context = build_contextual_evidence(
        current_payload=payload(geolocation={"latitude": -23.5505, "longitude": -46.6333}),
        admitted_at=NOW,
        prior_attempt_count=6,
        prior_device_attempt_count=2,
        prior_amount_median_cents=50_000,
        prior_history_span_seconds=172_800,
        distinct_counterparties={5: 2, 60: 4},
        prior_geolocation={
            "latitude": -22.9068,
            "longitude": -43.1729,
            "received_at": NOW - timedelta(hours=2),
        },
    )

    assert context["device"]["observation"] == "observed"
    assert context["profile"] == {
        "observation": "observed",
        "prior_attempt_count": 6,
        "history_span_seconds": 172_800,
        "median_amount_cents": 50_000,
    }
    assert context["diversity"]["60m"]["distinct_counterparty_count"] == 4
    assert context["geolocation"]["observation"] == "observed"
    assert context["geolocation"]["elapsed_seconds"] == 7200
    assert 350 < context["geolocation"]["distance_km"] < 370
    assert "-23.5505" not in str(context)
    assert "-43.1729" not in str(context)


def test_new_device_is_only_local_observation_and_invalid_legacy_geo_is_explicit():
    context = build_contextual_evidence(
        current_payload=payload(geolocation={"latitude": -23.5, "longitude": -46.6}),
        admitted_at=NOW,
        prior_attempt_count=3,
        prior_device_attempt_count=0,
        prior_amount_median_cents=10_000,
        prior_history_span_seconds=60,
        distinct_counterparties={5: 1, 60: 2},
        prior_geolocation={"latitude": "invalid", "longitude": -43.0, "received_at": NOW},
    )

    assert context["device"]["observation"] == "newly_observed"
    assert context["geolocation"] == {"observation": "invalid"}


def test_party_type_is_diagnostic_and_conservative():
    assert classify_party_type("123.456.789-09") == "pf"
    assert classify_party_type("00ABC123450001") == "pj"
    assert classify_party_type("not-identifiable") == "unknown"
