import os

import pytest


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)

from controllers.pix_contextual_risk_policy import PixContextualRiskPolicy  # noqa: E402
from controllers.pix_adaptive_risk_policy import PixAdaptiveRiskPolicy  # noqa: E402
from controllers.pix_policy_selector import build_risk_policy  # noqa: E402
from controllers.pix_risk_policy import PixRiskPolicy  # noqa: E402
from errors import RiskPolicyUnavailable  # noqa: E402


def test_adaptive_v3_is_the_default(monkeypatch):
    monkeypatch.delenv("PIX_RISK_POLICY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    assert isinstance(build_risk_policy(), PixAdaptiveRiskPolicy)


def test_v1_remains_available_by_explicit_legacy_selection(monkeypatch):
    monkeypatch.setenv("PIX_RISK_POLICY", "mvp-risk-experimental-v1")
    monkeypatch.setenv("APP_ENV", "production")

    assert isinstance(build_risk_policy(), PixRiskPolicy)


def test_v2_requires_explicit_test_environment_selection(monkeypatch):
    monkeypatch.setenv("PIX_RISK_POLICY", "mvp-risk-contextual-v2-candidate")
    monkeypatch.setenv("APP_ENV", "test")

    assert isinstance(build_risk_policy(), PixContextualRiskPolicy)

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RiskPolicyUnavailable):
        build_risk_policy()


def test_revised_contextual_policy_is_explicit_test_only_opt_in(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PIX_RISK_POLICY", "mvp-risk-contextual-v2-candidate-r2")

    policy = build_risk_policy()

    assert isinstance(policy, PixContextualRiskPolicy)
    assert policy.config.policy_version == "mvp-risk-contextual-v2-candidate-r2"
    assert policy.requires_approved_profile_history is True


def test_adaptive_v3_is_available_by_explicit_selection(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PIX_RISK_POLICY", "mvp-risk-adaptive-v3-r6")

    policy = build_risk_policy()

    assert isinstance(policy, PixAdaptiveRiskPolicy)
    assert policy.history_windows_minutes == (1, 5, 15, 60, 1440)

    monkeypatch.setenv("APP_ENV", "production")
    assert isinstance(build_risk_policy(), PixAdaptiveRiskPolicy)


def test_unknown_policy_fails_instead_of_approving(monkeypatch):
    monkeypatch.setenv("PIX_RISK_POLICY", "unknown")
    monkeypatch.setenv("APP_ENV", "test")

    with pytest.raises(RiskPolicyUnavailable):
        build_risk_policy()
