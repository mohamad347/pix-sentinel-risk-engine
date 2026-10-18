import os

import pytest


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://unit:unit@localhost:5432/unit_test_not_connected",
)
os.environ.setdefault("INTERNAL_TOKEN", "unit-test-token")

from controllers.pix_bootstrap_policy import BootstrapPolicyUnavailable, PixBootstrapPolicy  # noqa: E402


def test_bootstrap_policy_is_explicit_about_not_assessing_risk():
    decision = PixBootstrapPolicy(app_env="test").evaluate({})

    assert decision["status"] == "approved"
    assert decision["policy_version"] == "bootstrap-dev-v1"
    assert decision["metadata"]["reason_code"] == "BOOTSTRAP_NO_RISK_ASSESSMENT"


@pytest.mark.parametrize("app_env", ["production", "staging", ""])
def test_bootstrap_policy_is_blocked_outside_development_and_test(app_env):
    with pytest.raises(BootstrapPolicyUnavailable):
        PixBootstrapPolicy(app_env=app_env).evaluate({})
