import os

import pytest


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)

from controllers.pix_contextual_risk_policy import PixContextualRiskPolicy  # noqa: E402
from controllers.pix_policy_selector import build_risk_policy  # noqa: E402
from controllers.pix_risk_policy import PixRiskPolicy  # noqa: E402
from errors import RiskPolicyUnavailable  # noqa: E402


def test_v1_remains_the_default(monkeypatch):
    monkeypatch.delenv("PIX_RISK_POLICY", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    assert isinstance(build_risk_policy(), PixRiskPolicy)


def test_v2_requires_explicit_test_environment_selection(monkeypatch):
    monkeypatch.setenv("PIX_RISK_POLICY", "mvp-risk-contextual-v2-candidate")
    monkeypatch.setenv("APP_ENV", "test")

    assert isinstance(build_risk_policy(), PixContextualRiskPolicy)

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RiskPolicyUnavailable):
        build_risk_policy()


def test_unknown_policy_fails_instead_of_approving(monkeypatch):
    monkeypatch.setenv("PIX_RISK_POLICY", "unknown")
    monkeypatch.setenv("APP_ENV", "test")

    with pytest.raises(RiskPolicyUnavailable):
        build_risk_policy()
