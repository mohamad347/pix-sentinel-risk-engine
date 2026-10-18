import os

import pytest


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)

from controllers.pix_adaptive_risk_policy import (  # noqa: E402
    ADAPTIVE_POLICY_VERSION,
    PixAdaptiveRiskPolicy,
)
from errors import RiskPolicyUnavailable  # noqa: E402


def evidence(
    *,
    windows=None,
    prior_count=8,
    counterparty="observed",
    profile=None,
    relationship=None,
    access=None,
    temporal=None,
    beneficiary=None,
):
    default_windows = {
        "1m": {"attempt_count": 1, "amount_cents": 10_000},
        "5m": {"attempt_count": 1, "amount_cents": 10_000},
        "15m": {"attempt_count": 1, "amount_cents": 10_000},
        "60m": {"attempt_count": 1, "amount_cents": 10_000},
        "1440m": {"attempt_count": 1, "amount_cents": 10_000},
    }
    return {
        "schema_version": "pix-history-v1",
        "prior_attempt_count": prior_count,
        "counterparty": {"observation": counterparty},
        "windows": windows or default_windows,
        "contextual": {
            "schema_version": "pix-context-v3-r2-v2",
            "profile": profile
            or {
                "observation": "observed",
                "prior_attempt_count": 8,
                "history_span_seconds": 172_800,
                "median_amount_cents": 50_000,
                "p90_amount_cents": 100_000,
            },
            "relationship": relationship
            or {
                "prior_counterparty_attempt_count": 4,
                "pair_attempt_count_15m": 1,
                "distinct_counterparty_count_60m": 1,
            },
            "access": access
            or {
                "device_observation": "observed",
                "ip_observation": "observed",
                "channel_observation": "observed",
                "distinct_device_count_60m": 1,
                "distinct_ip_count_60m": 1,
                "device_distinct_payer_count_60m": 1,
                "ip_distinct_payer_count_60m": 1,
            },
            "temporal": temporal
            or {
                "transaction_clock_skew_seconds": 0,
                "nighttime_observation": "habitual",
                "geolocation": {"observation": "missing"},
            },
            "beneficiary": beneficiary
            or {
                "global_observation": "observed",
                "distinct_payer_count_60m": 1,
                "attempt_count_60m": 1,
                "destination_account_age_days": 365,
            },
            "party_types": {"payer": "pf", "payee": "pf"},
        },
    }


def evaluate(amount=10_000, snapshot=None):
    return PixAdaptiveRiskPolicy().evaluate(
        {"amount_cents": amount}, snapshot or evidence()
    )


def test_low_risk_habitual_payment_is_approved_with_six_capped_families():
    decision = evaluate()

    assert decision["status"] == "approved"
    assert decision["policy_version"] == ADAPTIVE_POLICY_VERSION
    assert "challenge" not in decision["metadata"]
    assert decision["evidence_snapshot"]["policy"]["families"] == {
        "exposure_velocity": 0,
        "profile": 0,
        "relationship": 0,
        "access": 0,
        "temporal": 0,
        "beneficiary_network": 0,
    }


def test_microburst_and_structuring_are_explainable_but_not_double_counted():
    windows = {
        "1m": {"attempt_count": 4, "amount_cents": 400_000},
        "5m": {"attempt_count": 7, "amount_cents": 700_000},
        "15m": {"attempt_count": 10, "amount_cents": 1_000_000},
        "60m": {"attempt_count": 12, "amount_cents": 1_600_000},
        "1440m": {"attempt_count": 12, "amount_cents": 1_600_000},
    }

    decision = evaluate(100_000, evidence(windows=windows))
    policy = decision["evidence_snapshot"]["policy"]

    assert policy["families"]["exposure_velocity"] == 25
    assert policy["risk_score"] == 25
    assert decision["status"] == "two_factor_required"
    assert {"MICROBURST_1M", "STRUCTURED_AGGREGATE_60M"} <= set(
        policy["triggered_signals"]
    )


def test_impossible_geo_velocity_with_access_takeover_is_rejected():
    snapshot = evidence(
        counterparty="not_observed",
        relationship={
            "prior_counterparty_attempt_count": 0,
            "pair_attempt_count_15m": 1,
            "distinct_counterparty_count_60m": 5,
        },
        access={
            "device_observation": "newly_observed",
            "ip_observation": "newly_observed",
            "channel_observation": "changed",
            "distinct_device_count_60m": 3,
            "distinct_ip_count_60m": 3,
            "device_distinct_payer_count_60m": 1,
            "ip_distinct_payer_count_60m": 1,
        },
        temporal={
            "transaction_clock_skew_seconds": 9_000,
            "nighttime_observation": "unusual",
            "geolocation": {
                "observation": "observed",
                "distance_km": 900.0,
                "elapsed_seconds": 1_800,
                "speed_kmh": 1_800.0,
            },
        },
    )

    decision = evaluate(100_000, snapshot)
    policy = decision["evidence_snapshot"]["policy"]

    assert policy["families"]["relationship"] == 12
    assert policy["families"]["access"] == 20
    assert policy["families"]["temporal"] == 25
    assert policy["risk_score"] == 57
    assert policy["critical_geolocation_safeguard"] is True
    assert "CRITICAL_GEO_ACCESS_MISMATCH" in policy["rejection_safeguards"]
    assert decision["status"] == "rejected"
    assert decision["metadata"]["reason_code"] == "ADAPTIVE_IMPOSSIBLE_TRAVEL"
    assert "challenge" not in decision["metadata"]


def test_recipient_fanin_and_young_account_raise_verification_not_rejection():
    decision = evaluate(
        400_000,
        evidence(
            beneficiary={
                "global_observation": "observed",
                "distinct_payer_count_60m": 6,
                "attempt_count_60m": 8,
                "destination_account_age_days": 2,
            }
        ),
    )
    policy = decision["evidence_snapshot"]["policy"]

    assert policy["families"]["beneficiary_network"] == 20
    assert policy["risk_score"] == 35
    assert decision["status"] == "two_factor_required"
    assert {"RECIPIENT_FAN_IN_60M", "YOUNG_DESTINATION_ACCOUNT"} <= set(
        policy["triggered_signals"]
    )


def test_many_independent_high_risk_families_can_reject_without_a_burst():
    contextual = evidence(
        counterparty="not_observed",
        profile={
            "observation": "observed",
            "prior_attempt_count": 8,
            "history_span_seconds": 172_800,
            "median_amount_cents": 10_000,
            "p90_amount_cents": 20_000,
        },
        relationship={
            "prior_counterparty_attempt_count": 0,
            "pair_attempt_count_15m": 1,
            "distinct_counterparty_count_60m": 8,
        },
        access={
            "device_observation": "newly_observed",
            "ip_observation": "newly_observed",
            "channel_observation": "changed",
            "distinct_device_count_60m": 3,
            "distinct_ip_count_60m": 3,
            "device_distinct_payer_count_60m": 1,
            "ip_distinct_payer_count_60m": 1,
        },
        beneficiary={
            "global_observation": "observed",
            "distinct_payer_count_60m": 8,
            "attempt_count_60m": 10,
            "destination_account_age_days": 2,
        },
    )

    high_context = evaluate(300_000, contextual)
    assert high_context["evidence_snapshot"]["policy"]["risk_score"] >= 60
    assert high_context["status"] == "rejected"
    assert high_context["metadata"]["reason_code"] == (
        "ADAPTIVE_CONVERGENT_CRITICAL_RISK"
    )
    assert "CONVERGENT_CRITICAL_RISK" in high_context["evidence_snapshot"]["policy"][
        "rejection_safeguards"
    ]

    burst = evidence(
        windows={
            "1m": {"attempt_count": 4, "amount_cents": 1_500_000},
            "5m": {"attempt_count": 8, "amount_cents": 3_000_000},
            "15m": {"attempt_count": 8, "amount_cents": 3_000_000},
            "60m": {"attempt_count": 8, "amount_cents": 3_000_000},
            "1440m": {"attempt_count": 8, "amount_cents": 3_000_000},
        }
    )
    rejected = evaluate(375_000, burst)
    assert rejected["status"] == "rejected"
    assert "challenge" not in rejected["metadata"]
    assert rejected["evidence_snapshot"]["policy"]["critical_burst_safeguard"] is True


def test_impossible_geo_velocity_alone_requires_2fa_instead_of_rejecting():
    decision = evaluate(
        snapshot=evidence(
            temporal={
                "transaction_clock_skew_seconds": 0,
                "nighttime_observation": "not_nighttime",
                "geolocation": {
                    "observation": "observed",
                    "distance_km": 400.0,
                    "elapsed_seconds": 600,
                    "speed_kmh": 2_400.0,
                },
            }
        )
    )

    policy = decision["evidence_snapshot"]["policy"]
    assert policy["families"]["temporal"] == 25
    assert "IMPOSSIBLE_GEO_VELOCITY" in policy["triggered_signals"]
    assert decision["status"] == "two_factor_required"
    assert decision["metadata"]["challenge"]["type"] == "push_confirmation"


def test_elevated_but_not_impossible_geo_velocity_is_a_bounded_signal():
    decision = evaluate(
        snapshot=evidence(
            temporal={
                "transaction_clock_skew_seconds": 0,
                "nighttime_observation": "not_nighttime",
                "geolocation": {
                    "observation": "observed",
                    "distance_km": 100.0,
                    "elapsed_seconds": 600,
                    "speed_kmh": 600.0,
                },
            }
        )
    )

    policy = decision["evidence_snapshot"]["policy"]
    assert policy["families"]["temporal"] == 10
    assert "ELEVATED_GEO_VELOCITY" in policy["triggered_signals"]
    assert decision["status"] == "approved"


def test_exactly_one_thousand_kmh_is_elevated_not_critical():
    decision = evaluate(
        snapshot=evidence(
            temporal={
                "transaction_clock_skew_seconds": 0,
                "nighttime_observation": "not_nighttime",
                "geolocation": {
                    "observation": "observed",
                    "distance_km": 100.0,
                    "elapsed_seconds": 360,
                    "speed_kmh": 1_000.0,
                },
            }
        )
    )

    policy = decision["evidence_snapshot"]["policy"]
    assert policy["families"]["temporal"] == 10
    assert policy["critical_geolocation_safeguard"] is False
    assert decision["status"] == "approved"


def test_score_sixty_with_only_three_families_requires_2fa_not_rejection():
    snapshot = evidence(
        access={
            "device_observation": "newly_observed",
            "ip_observation": "newly_observed",
            "channel_observation": "changed",
            "distinct_device_count_60m": 3,
            "distinct_ip_count_60m": 3,
            "device_distinct_payer_count_60m": 1,
            "ip_distinct_payer_count_60m": 1,
        }
    )

    decision = evaluate(500_000, snapshot)
    policy = decision["evidence_snapshot"]["policy"]

    assert policy["risk_score"] == 60
    assert policy["active_risk_family_count"] == 3
    assert policy["convergent_critical_risk"] is False
    assert decision["status"] == "two_factor_required"


def test_exposure_only_2fa_recommends_standard_push_confirmation():
    decision = evaluate(500_000)

    assert decision["status"] == "two_factor_required"
    assert decision["metadata"]["challenge"] == {
        "type": "push_confirmation",
        "level": "standard",
        "status": "recommended",
    }


def test_malformed_or_wrong_version_evidence_fails_closed_technically():
    snapshot = evidence()
    snapshot["contextual"]["schema_version"] = "unknown"

    with pytest.raises(RiskPolicyUnavailable):
        evaluate(snapshot=snapshot)


def test_device_shared_across_three_payers_is_a_global_access_signal():
    snapshot = evidence(
        prior_count=0,
        counterparty="insufficient_history",
        access={
            "device_observation": "insufficient_history",
            "ip_observation": "insufficient_history",
            "channel_observation": "insufficient_history",
            "distinct_device_count_60m": 1,
            "distinct_ip_count_60m": 1,
            "device_distinct_payer_count_60m": 3,
            "ip_distinct_payer_count_60m": 1,
        },
    )

    decision = evaluate(snapshot=snapshot)

    assert decision["evidence_snapshot"]["policy"]["families"]["access"] == 15
    assert "DEVICE_SHARED_ACROSS_PAYERS_60M" in decision["evidence_snapshot"][
        "policy"
    ]["triggered_signals"]
