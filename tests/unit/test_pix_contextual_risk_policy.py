import os

import pytest


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)

from controllers.pix_contextual_risk_policy import (  # noqa: E402
    CONTEXTUAL_POLICY_VERSION,
    DEFAULT_CONTEXTUAL_RISK_CONFIG,
    ContextualRiskConfig,
    ContextualScoreWeights,
    PixContextualRiskPolicy,
)
from errors import RiskPolicyUnavailable  # noqa: E402


def evidence(
    *,
    count_5m=1,
    amount_5m=10_000,
    amount_60m=10_000,
    prior_count=0,
    counterparty="insufficient_history",
    device="insufficient_history",
    device_prior_count=0,
    profile="insufficient_history",
    profile_count=0,
    profile_span_seconds=0,
    median_amount=None,
    distinct_60m=1,
    geo="missing",
):
    return {
        "schema_version": "pix-history-v1",
        "admitted_at": "2026-10-18T15:00:00.000000Z",
        "prior_attempt_count": prior_count,
        "is_first_observed_attempt": prior_count == 0,
        "counterparty": {
            "schema_version": "pix-counterparty-v1",
            "prior_attempt_count": 0,
            "observation": counterparty,
            "is_previously_observed": None,
            "is_unobserved_with_payer_history": None,
        },
        "windows": {
            "5m": {"attempt_count": count_5m, "amount_cents": amount_5m},
            "60m": {"attempt_count": count_5m, "amount_cents": amount_60m},
        },
        "contextual": {
            "schema_version": "pix-context-v2-candidate-v1",
            "device": {
                "observation": device,
                "prior_attempt_count": device_prior_count,
            },
            "profile": {
                "observation": profile,
                "prior_attempt_count": profile_count,
                "history_span_seconds": profile_span_seconds,
                "median_amount_cents": median_amount,
            },
            "diversity": {
                "60m": {"distinct_counterparty_count": distinct_60m},
            },
            "geolocation": {"observation": geo},
            "source_availability": {"channel": "observed", "ip": "observed"},
            "party_types": {"payer": "unknown", "payee": "unknown"},
        },
    }


def evaluate(amount, snapshot):
    return PixContextualRiskPolicy().evaluate({"amount_cents": amount}, snapshot)


def test_cold_start_and_optional_missing_context_are_neutral():
    decision = evaluate(10_000, evidence())

    assert decision["status"] == "approved"
    assert decision["policy_version"] == CONTEXTUAL_POLICY_VERSION
    assert decision["evidence_snapshot"]["policy"]["risk_score"] == 0
    assert decision["evidence_snapshot"]["policy"]["families"] == {
        "exposure_velocity": 0,
        "profile": 0,
        "counterparty_diversity": 0,
        "device": 0,
        "geolocation": 0,
    }
    assert "risk_score" not in decision["metadata"]


def test_score_changes_v1_high_value_boundary_without_rejecting_or_exposing_score():
    decision = evaluate(500_000, evidence(amount_5m=500_000, amount_60m=500_000))

    assert decision["status"] == "approved"
    assert decision["evidence_snapshot"]["policy"]["risk_score"] == 15
    assert decision["metadata"]["reason_code"] == "CONTEXTUAL_RISK_LOW"
    assert set(decision["metadata"]) == {
        "schema_version",
        "policy_version",
        "reason_code",
        "reason_description",
    }


def test_profile_spike_uses_prior_only_and_recommends_verification():
    snapshot = evidence(
        prior_count=5,
        counterparty="observed",
        device="observed",
        device_prior_count=5,
        profile="observed",
        profile_count=5,
        profile_span_seconds=86_400,
        median_amount=50_000,
    )

    decision = evaluate(500_000, snapshot)

    assert decision["status"] == "two_factor_required"
    assert decision["evidence_snapshot"]["policy"]["risk_score"] == 40
    assert decision["evidence_snapshot"]["policy"]["families"]["profile"] == 25
    assert decision["evidence_snapshot"]["policy"]["profile_ratio_basis_points"] == 100_000


def test_correlated_exposure_signals_are_capped_and_do_not_reject_alone():
    decision = evaluate(
        375_000,
        evidence(
            count_5m=8,
            amount_5m=3_000_000,
            amount_60m=3_000_000,
            prior_count=7,
            counterparty="observed",
            device="observed",
            device_prior_count=7,
        ),
    )

    assert decision["evidence_snapshot"]["policy"]["families"]["exposure_velocity"] == 45
    assert decision["evidence_snapshot"]["policy"]["risk_score"] == 45
    assert decision["status"] == "two_factor_required"


def test_critical_score_requires_and_honors_burst_safeguard():
    snapshot = evidence(
        count_5m=8,
        amount_5m=3_000_000,
        amount_60m=3_000_000,
        prior_count=7,
        counterparty="not_observed",
        device="newly_observed",
        device_prior_count=0,
        profile="observed",
        profile_count=7,
        profile_span_seconds=86_400,
        median_amount=40_000,
        distinct_60m=8,
        geo="observed",
    )

    decision = evaluate(375_000, snapshot)

    policy = decision["evidence_snapshot"]["policy"]
    assert policy["families"] == {
        "exposure_velocity": 45,
        "profile": 25,
        "counterparty_diversity": 15,
        "device": 15,
        "geolocation": 0,
    }
    assert policy["risk_score"] == 100
    assert decision["status"] == "rejected"
    assert decision["metadata"]["reason_code"] == "CONTEXTUAL_CRITICAL_COMBINATION"


def test_context_ablation_removes_contextual_power_without_changing_exposure():
    config = ContextualRiskConfig.ablation()
    snapshot = evidence(
        prior_count=5,
        counterparty="not_observed",
        device="newly_observed",
        profile="observed",
        profile_count=5,
        profile_span_seconds=86_400,
        median_amount=10_000,
        distinct_60m=4,
    )

    decision = PixContextualRiskPolicy(config).evaluate({"amount_cents": 100_000}, snapshot)

    assert decision["evidence_snapshot"]["policy"]["risk_score"] == 0
    assert decision["status"] == "approved"


def test_adverse_exposure_is_monotonic_and_geo_remains_neutral():
    scores = [
        evaluate(10_000, evidence(count_5m=count, amount_5m=total, amount_60m=total))[
            "evidence_snapshot"
        ]["policy"]["risk_score"]
        for count, total in [(1, 10_000), (4, 40_000), (8, 3_000_000)]
    ]
    far_geo = evaluate(10_000, evidence(geo="observed"))

    assert scores == sorted(scores)
    assert far_geo["evidence_snapshot"]["policy"]["families"]["geolocation"] == 0
    assert far_geo["evidence_snapshot"]["policy"]["risk_score"] == 0


def test_malformed_context_fails_technically_instead_of_becoming_missing():
    snapshot = evidence()
    snapshot["contextual"]["geolocation"]["observation"] = "untrusted-state"

    with pytest.raises(RiskPolicyUnavailable):
        evaluate(10_000, snapshot)


def test_score_weights_are_typed_central_configuration():
    with pytest.raises(RiskPolicyUnavailable):
        ContextualScoreWeights(exposure_cap=20)


@pytest.mark.parametrize(
    "override",
    [
        {"two_factor_score": 0},
        {"reject_score": 101},
        {"two_factor_score": 70, "reject_score": 60},
        {"profile_min_prior_attempts": 0},
    ],
)
def test_invalid_configuration_fails_technically(override):
    values = {
        field: getattr(DEFAULT_CONTEXTUAL_RISK_CONFIG, field)
        for field in DEFAULT_CONTEXTUAL_RISK_CONFIG.__dataclass_fields__
    }
    values.update(override)

    with pytest.raises(RiskPolicyUnavailable):
        ContextualRiskConfig(**values)

