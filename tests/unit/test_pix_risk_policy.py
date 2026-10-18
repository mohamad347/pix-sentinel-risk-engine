import os

import pytest


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)

from controllers.pix_risk_policy import (  # noqa: E402
    DEFAULT_RISK_POLICY_CONFIG,
    POLICY_VERSION,
    PixRiskPolicy,
    RiskPolicyConfig,
)
from errors import RiskPolicyUnavailable  # noqa: E402


def evidence(count_5m=1, total_5m=100_00, total_60m=100_00):
    return {
        "schema_version": "pix-history-v1",
        "admitted_at": "2026-10-18T15:00:00.000000Z",
        "prior_attempt_count": max(count_5m - 1, 0),
        "is_first_observed_attempt": count_5m == 1,
        "windows": {
            "5m": {"attempt_count": count_5m, "amount_cents": total_5m},
            "60m": {"attempt_count": count_5m, "amount_cents": total_60m},
        },
    }


def evaluate(amount_cents, history):
    return PixRiskPolicy().evaluate({"amount_cents": amount_cents}, history)


def test_moderate_cold_start_is_approved():
    decision = evaluate(100_00, evidence())

    assert decision["status"] == "approved"
    assert decision["policy_version"] == "mvp-risk-experimental-v1"
    assert decision["metadata"]["reason_code"] == "RISK_WITHIN_EXPERIMENTAL_POLICY"


def test_single_high_value_requires_2fa_but_is_not_rejected():
    decision = evaluate(500_000, evidence(total_5m=500_000, total_60m=500_000))

    assert decision["status"] == "two_factor_required"
    assert decision["metadata"]["reason_code"] == "ADDITIONAL_VERIFICATION_RECOMMENDED"
    assert "HIGH_CURRENT_VALUE" in decision["evidence_snapshot"]["policy"]["triggered_signals"]


@pytest.mark.parametrize(
    "count_5m,expected_status",
    [
        (DEFAULT_RISK_POLICY_CONFIG.count_2fa_5m - 1, "approved"),
        (DEFAULT_RISK_POLICY_CONFIG.count_2fa_5m, "two_factor_required"),
    ],
)
def test_2fa_count_boundary(count_5m, expected_status):
    assert evaluate(100, evidence(count_5m=count_5m))["status"] == expected_status


@pytest.mark.parametrize(
    "total_60m,expected_status",
    [
        (DEFAULT_RISK_POLICY_CONFIG.total_2fa_60m_cents - 1, "approved"),
        (DEFAULT_RISK_POLICY_CONFIG.total_2fa_60m_cents, "two_factor_required"),
    ],
)
def test_2fa_total_boundary_is_exact_in_cents(total_60m, expected_status):
    assert evaluate(100, evidence(total_60m=total_60m))["status"] == expected_status


def test_reject_count_without_critical_volume_does_not_reject():
    decision = evaluate(
        100,
        evidence(
            count_5m=DEFAULT_RISK_POLICY_CONFIG.count_reject_5m,
            total_5m=DEFAULT_RISK_POLICY_CONFIG.total_reject_5m_cents - 1,
        ),
    )

    assert decision["status"] == "two_factor_required"


def test_critical_volume_without_reject_count_does_not_reject():
    decision = evaluate(
        100,
        evidence(
            count_5m=DEFAULT_RISK_POLICY_CONFIG.count_reject_5m - 1,
            total_5m=DEFAULT_RISK_POLICY_CONFIG.total_reject_5m_cents,
        ),
    )

    assert decision["status"] == "two_factor_required"


def test_both_critical_signals_at_boundary_reject_with_precedence():
    decision = evaluate(
        500_000,
        evidence(
            count_5m=DEFAULT_RISK_POLICY_CONFIG.count_reject_5m,
            total_5m=DEFAULT_RISK_POLICY_CONFIG.total_reject_5m_cents,
            total_60m=DEFAULT_RISK_POLICY_CONFIG.total_2fa_60m_cents,
        ),
    )

    assert decision["status"] == "rejected"
    assert decision["metadata"]["reason_code"] == "SUSPICIOUS_BURST_AND_VOLUME"


def test_large_single_transfer_never_rejects_only_for_value():
    decision = evaluate(
        3_000_000,
        evidence(count_5m=1, total_5m=3_000_000, total_60m=3_000_000),
    )

    assert decision["status"] == "two_factor_required"


def test_policy_configuration_is_typed_and_replaceable():
    config = RiskPolicyConfig(
        policy_version="custom-test-v1",
        value_2fa_cents=10,
        count_2fa_5m=20,
        total_2fa_60m_cents=30,
        count_reject_5m=40,
        total_reject_5m_cents=50,
    )

    decision = PixRiskPolicy(config).evaluate({"amount_cents": 10}, evidence())

    assert decision["status"] == "two_factor_required"
    assert decision["policy_version"] == "custom-test-v1"


@pytest.mark.parametrize(
    "transaction_data,history",
    [
        ({}, evidence()),
        ({"amount_cents": 100}, {}),
        ({"amount_cents": 100}, evidence(count_5m=-1)),
    ],
)
def test_missing_or_invalid_inputs_fail_technically(transaction_data, history):
    with pytest.raises(RiskPolicyUnavailable):
        PixRiskPolicy().evaluate(transaction_data, history)


def test_invalid_configuration_fails_instead_of_falling_back_to_approval():
    with pytest.raises(RiskPolicyUnavailable):
        RiskPolicyConfig(
            policy_version=POLICY_VERSION,
            value_2fa_cents=0,
            count_2fa_5m=4,
            total_2fa_60m_cents=1_500_000,
            count_reject_5m=8,
            total_reject_5m_cents=3_000_000,
        )
