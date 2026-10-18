import os

from controllers.pix_contextual_risk_policy import (
    CONTEXTUAL_POLICY_VERSION,
    PixContextualRiskPolicy,
)
from controllers.pix_risk_policy import POLICY_VERSION, PixRiskPolicy
from errors import RiskPolicyUnavailable


POLICY_ENVIRONMENT_VARIABLE = "PIX_RISK_POLICY"


def build_risk_policy():
    selected = os.environ.get(POLICY_ENVIRONMENT_VARIABLE, POLICY_VERSION)
    if selected == POLICY_VERSION:
        return PixRiskPolicy()
    if selected == CONTEXTUAL_POLICY_VERSION and os.environ.get("APP_ENV") == "test":
        return PixContextualRiskPolicy()
    raise RiskPolicyUnavailable()
