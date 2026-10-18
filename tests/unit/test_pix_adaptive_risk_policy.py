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
    party_types=None,
    account_context=None,
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
            "schema_version": "pix-context-v3-r4-v4",
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
            "party_types": party_types or {"payer": "pf", "payee": "pf"},
            "account_context": account_context
            or {
                "source_account_age_days": 365,
                "is_self_transfer": False,
            },
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


def test_exposure_uses_the_strongest_non_redundant_signal_per_dimension():
    windows = {
        "1m": {"attempt_count": 4, "amount_cents": 400_000},
        "5m": {"attempt_count": 7, "amount_cents": 700_000},
        "15m": {"attempt_count": 10, "amount_cents": 1_000_000},
        "60m": {"attempt_count": 12, "amount_cents": 1_600_000},
        "1440m": {"attempt_count": 12, "amount_cents": 1_600_000},
    }

    decision = evaluate(100_000, evidence(windows=windows))
    policy = decision["evidence_snapshot"]["policy"]

    assert policy["families"]["exposure_velocity"] == 35
    assert policy["risk_score"] == 35
    assert decision["status"] == "two_factor_required"
    assert {"MICROBURST_1M", "STRUCTURED_AGGREGATE_60M"} <= set(
        policy["triggered_signals"]
    )
    assert "ELEVATED_ATTEMPT_TOTAL_60M" not in policy["triggered_signals"]


@pytest.mark.parametrize(
    ("payer_type", "count_1m", "count_5m", "expected_score", "expected_signal"),
    (
        ("pf", 1, 4, 0, None),
        ("pf", 1, 7, 0, None),
        ("pf", 4, 4, 25, "MICROBURST_1M"),
        ("pj", 1, 8, 0, None),
        ("pj", 6, 8, 25, "MICROBURST_1M"),
    ),
)
def test_only_microburst_scores_short_window_frequency_with_pf_pj_thresholds(
    payer_type, count_1m, count_5m, expected_score, expected_signal
):
    windows = {
        "1m": {"attempt_count": count_1m, "amount_cents": 10_000},
        "5m": {"attempt_count": count_5m, "amount_cents": 20_000},
        "15m": {"attempt_count": count_5m, "amount_cents": 20_000},
        "60m": {"attempt_count": count_5m, "amount_cents": 20_000},
        "1440m": {"attempt_count": count_5m, "amount_cents": 20_000},
    }

    decision = evaluate(
        snapshot=evidence(
            windows=windows,
            party_types={"payer": payer_type, "payee": "pf"},
        )
    )
    policy = decision["evidence_snapshot"]["policy"]

    assert policy["families"]["exposure_velocity"] == expected_score
    if expected_signal is None:
        assert decision["status"] == "approved"
        assert "MICROBURST_1M" not in policy["triggered_signals"]
    else:
        assert expected_signal in policy["triggered_signals"]
        assert decision["status"] == (
            "two_factor_required" if expected_score >= 25 else "approved"
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


def test_pf_and_pj_use_different_absolute_exposure_thresholds():
    insufficient_profile = {
        "observation": "insufficient_history",
        "prior_attempt_count": 0,
        "history_span_seconds": 0,
        "median_amount_cents": None,
        "p90_amount_cents": None,
    }
    pf = evaluate(600_000, evidence(profile=insufficient_profile))
    pj = evaluate(
        600_000,
        evidence(
            profile=insufficient_profile,
            party_types={"payer": "pj", "payee": "pf"},
        ),
    )

    assert pf["status"] == "two_factor_required"
    assert "ELEVATED_CURRENT_VALUE" in pf["evidence_snapshot"]["policy"][
        "triggered_signals"
    ]
    assert pj["status"] == "approved"
    assert "ELEVATED_CURRENT_VALUE" not in pj["evidence_snapshot"]["policy"][
        "triggered_signals"
    ]


def test_pj_fan_out_and_pj_recipient_fan_in_use_business_thresholds():
    pj_payer = evaluate(
        snapshot=evidence(
            relationship={
                "prior_counterparty_attempt_count": 0,
                "pair_attempt_count_15m": 1,
                "distinct_counterparty_count_60m": 5,
            },
            party_types={"payer": "pj", "payee": "pf"},
        )
    )
    pj_payee = evaluate(
        snapshot=evidence(
            beneficiary={
                "global_observation": "observed",
                "distinct_payer_count_60m": 6,
                "attempt_count_60m": 8,
                "destination_account_age_days": 365,
            },
            party_types={"payer": "pf", "payee": "pj"},
        )
    )

    assert "ELEVATED_FAN_OUT_60M" not in pj_payer["evidence_snapshot"]["policy"][
        "triggered_signals"
    ]
    assert "RECIPIENT_FAN_IN_60M" not in pj_payee["evidence_snapshot"]["policy"][
        "triggered_signals"
    ]


def test_self_transfer_does_not_score_recipient_novelty_or_young_destination():
    decision = evaluate(
        400_000,
        evidence(
            counterparty="not_observed",
            beneficiary={
                "global_observation": "not_observed",
                "distinct_payer_count_60m": 0,
                "attempt_count_60m": 0,
                "destination_account_age_days": 1,
            },
            account_context={
                "source_account_age_days": 365,
                "is_self_transfer": True,
            },
        ),
    )

    policy = decision["evidence_snapshot"]["policy"]
    assert policy["families"]["relationship"] == 0
    assert policy["families"]["beneficiary_network"] == 0
    assert "NEW_COUNTERPARTY" not in policy["triggered_signals"]
    assert "RECIPIENT_FIRST_SYSTEM_OBSERVATION" not in policy["triggered_signals"]
    assert "YOUNG_DESTINATION_ACCOUNT" not in policy["triggered_signals"]


@pytest.mark.parametrize(
    ("payer_type", "amount"),
    (("pf", 300_000), ("pj", 1_000_000), ("unknown", 300_000)),
)
def test_first_observation_of_young_source_account_deterministically_requires_2fa(
    payer_type, amount
):
    decision = evaluate(
        amount,
        evidence(
            prior_count=0,
            counterparty="insufficient_history",
            profile={
                "observation": "insufficient_history",
                "prior_attempt_count": 0,
                "history_span_seconds": 0,
                "median_amount_cents": None,
                "p90_amount_cents": None,
            },
            access={
                "device_observation": "insufficient_history",
                "ip_observation": "insufficient_history",
                "channel_observation": "insufficient_history",
                "distinct_device_count_60m": 1,
                "distinct_ip_count_60m": 1,
                "device_distinct_payer_count_60m": 1,
                "ip_distinct_payer_count_60m": 1,
            },
            beneficiary={
                "global_observation": "observed",
                "distinct_payer_count_60m": 1,
                "attempt_count_60m": 1,
                "destination_account_age_days": 365,
            },
            party_types={"payer": payer_type, "payee": "pf"},
            account_context={
                "source_account_age_days": 5,
                "is_self_transfer": False,
            },
        ),
    )

    policy = decision["evidence_snapshot"]["policy"]
    assert decision["status"] == "two_factor_required"
    assert decision["metadata"]["reason_code"] == "ADAPTIVE_COLD_START_VERIFICATION"
    assert policy["cold_start_verification_safeguard"] is True
    assert "FIRST_OBSERVED_YOUNG_SOURCE_ACCOUNT" in policy["triggered_signals"]


def test_enhanced_facial_liveness_is_never_recommended_for_pj():
    takeover_access = {
        "device_observation": "newly_observed",
        "ip_observation": "newly_observed",
        "channel_observation": "observed",
        "distinct_device_count_60m": 1,
        "distinct_ip_count_60m": 1,
        "device_distinct_payer_count_60m": 1,
        "ip_distinct_payer_count_60m": 1,
    }
    pf = evaluate(
        200_000,
        evidence(access=takeover_access, party_types={"payer": "pf", "payee": "pf"}),
    )
    pj = evaluate(
        200_000,
        evidence(access=takeover_access, party_types={"payer": "pj", "payee": "pf"}),
    )

    assert pf["metadata"]["challenge"]["type"] == "facial_liveness"
    assert pj["metadata"]["challenge"]["type"] == "push_confirmation"


def test_new_device_ip_and_counterparty_require_verification_at_score_23():
    decision = evaluate(
        snapshot=evidence(
            counterparty="not_observed",
            access={
                "device_observation": "newly_observed",
                "ip_observation": "newly_observed",
                "channel_observation": "observed",
                "distinct_device_count_60m": 1,
                "distinct_ip_count_60m": 1,
                "device_distinct_payer_count_60m": 1,
                "ip_distinct_payer_count_60m": 1,
            },
        )
    )

    policy = decision["evidence_snapshot"]["policy"]
    assert policy["risk_score"] == 23
    assert policy["access_relationship_verification_safeguard"] is True
    assert decision["status"] == "two_factor_required"
    assert decision["metadata"]["reason_code"] == (
        "ADAPTIVE_ACCESS_RELATIONSHIP_VERIFICATION"
    )
