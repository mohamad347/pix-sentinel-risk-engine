import os

from controllers.pix_adaptive_risk_policy import (
    ADAPTIVE_POLICY_VERSION,
    PixAdaptiveRiskPolicy,
)
from controllers.pix_contextual_risk_policy import (
    CONTEXTUAL_POLICY_VERSION,
    CONTEXTUAL_POLICY_VERSION_R2,
    REVISED_CONTEXTUAL_RISK_CONFIG,
    PixContextualRiskPolicy,
)
from controllers.pix_risk_policy import POLICY_VERSION, PixRiskPolicy
from errors import RiskPolicyUnavailable


POLICY_ENVIRONMENT_VARIABLE = "PIX_RISK_POLICY"


def build_risk_policy():
    selected = os.environ.get(POLICY_ENVIRONMENT_VARIABLE, ADAPTIVE_POLICY_VERSION)
    if selected == ADAPTIVE_POLICY_VERSION:
        return PixAdaptiveRiskPolicy()
    if selected == POLICY_VERSION:
        return PixRiskPolicy()
    if selected == CONTEXTUAL_POLICY_VERSION and os.environ.get("APP_ENV") == "test":
        return PixContextualRiskPolicy()
    if selected == CONTEXTUAL_POLICY_VERSION_R2 and os.environ.get("APP_ENV") == "test":
        return PixContextualRiskPolicy(REVISED_CONTEXTUAL_RISK_CONFIG)
    raise RiskPolicyUnavailable()
