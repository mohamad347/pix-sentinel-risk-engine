from datetime import datetime, timedelta, timezone

from utils.pix_context import (
    build_adaptive_contextual_evidence,
    build_contextual_evidence,
    classify_party_type,
)


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
        profile_attempt_count=4,
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
        "prior_attempt_count": 4,
        "history_span_seconds": 172_800,
        "median_amount_cents": 50_000,
    }
    assert context["diversity"]["60m"]["distinct_counterparty_count"] == 4
    assert context["geolocation"]["observation"] == "observed"
    assert context["geolocation"]["elapsed_seconds"] == 7200
    assert 350 < context["geolocation"]["distance_km"] < 370
    assert 175 < context["geolocation"]["speed_kmh"] < 185
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


def test_adaptive_context_derives_access_temporal_and_network_without_raw_pii():
    current = payload(geolocation={"latitude": -23.5505, "longitude": -46.6333})
    current["transaction_date"] = "2026-10-18T11:30:00-03:00"
    current["destination_account"]["opening_date"] = "2026-10-16T12:00:00-03:00"
    context = build_adaptive_contextual_evidence(
        current_payload=current,
        admitted_at=NOW,
        prior_attempt_count=8,
        prior_counterparty_attempt_count=0,
        prior_device_attempt_count=0,
        prior_ip_attempt_count=0,
        prior_channel_attempt_count=0,
        prior_amount_median_cents=50_000,
        prior_amount_p90_cents=100_000,
        prior_history_span_seconds=172_800,
        profile_attempt_count=8,
        prior_nighttime_attempt_count=0,
        pair_attempt_count_15m=3,
        distinct_counterparties={60: 4},
        distinct_devices={60: 3},
        distinct_ips={60: 2},
        prior_geolocation={
            "latitude": -22.9068,
            "longitude": -43.1729,
            "received_at": NOW - timedelta(minutes=30),
        },
        beneficiary_context={
            "global_observation": "observed",
            "distinct_payer_count_60m": 6,
            "attempt_count_60m": 8,
        },
        access_network_context={
            "device_distinct_payer_count_60m": 3,
            "ip_distinct_payer_count_60m": 5,
        },
    )

    assert context["access"] == {
        "device_observation": "newly_observed",
        "ip_observation": "newly_observed",
        "channel_observation": "changed",
        "distinct_device_count_60m": 3,
        "distinct_ip_count_60m": 2,
        "device_distinct_payer_count_60m": 3,
        "ip_distinct_payer_count_60m": 5,
    }
    assert context["relationship"]["distinct_counterparty_count_60m"] == 4
    assert context["beneficiary"]["destination_account_age_days"] == 2
    assert context["temporal"]["transaction_clock_skew_seconds"] == 1800
    assert context["temporal"]["geolocation"]["observation"] == "observed"
    assert context["temporal"]["geolocation"]["speed_kmh"] > 700
    serialized = str(context)
    assert "198.51.100.23" not in serialized
    assert "device-new" not in serialized
    assert "12345678901" not in serialized
