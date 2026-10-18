from copy import deepcopy
from dataclasses import dataclass, fields

from errors import RiskPolicyUnavailable


POLICY_VERSION = "mvp-risk-experimental-v1"


@dataclass(frozen=True)
class RiskPolicyConfig:
    """Experimental cent/count thresholds selected for the hackathon MVP."""

    policy_version: str
    value_2fa_cents: int
    count_2fa_5m: int
    total_2fa_60m_cents: int
    count_reject_5m: int
    total_reject_5m_cents: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise RiskPolicyUnavailable()
        for field in fields(self):
            if field.name == "policy_version":
                continue
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RiskPolicyUnavailable()
        if self.count_reject_5m < self.count_2fa_5m:
            raise RiskPolicyUnavailable()


DEFAULT_RISK_POLICY_CONFIG = RiskPolicyConfig(
    policy_version=POLICY_VERSION,
    value_2fa_cents=500_000,
    count_2fa_5m=4,
    total_2fa_60m_cents=1_500_000,
    count_reject_5m=8,
    total_reject_5m_cents=3_000_000,
)


class PixRiskPolicy:
    def __init__(self, config: RiskPolicyConfig = DEFAULT_RISK_POLICY_CONFIG) -> None:
        self.config = config

    def evaluate(self, transaction_data: dict, evidence_snapshot: dict) -> dict:
        amount_cents = self._positive_int(transaction_data.get("amount_cents"))
        count_5m = self._metric(evidence_snapshot, "5m", "attempt_count")
        total_5m = self._metric(evidence_snapshot, "5m", "amount_cents")
        total_60m = self._metric(evidence_snapshot, "60m", "amount_cents")

        signals = []
        if amount_cents >= self.config.value_2fa_cents:
            signals.append("HIGH_CURRENT_VALUE")
        if count_5m >= self.config.count_2fa_5m:
            signals.append("HIGH_ATTEMPT_COUNT_5M")
        if total_60m >= self.config.total_2fa_60m_cents:
            signals.append("HIGH_ATTEMPT_TOTAL_60M")

        critical_count = count_5m >= self.config.count_reject_5m
        critical_volume = total_5m >= self.config.total_reject_5m_cents
        if critical_count:
            signals.append("CRITICAL_ATTEMPT_COUNT_5M")
        if critical_volume:
            signals.append("CRITICAL_ATTEMPT_TOTAL_5M")

        if critical_count and critical_volume:
            status = "rejected"
            reason_code = "SUSPICIOUS_BURST_AND_VOLUME"
            reason_description = (
                "The request matched the experimental critical-risk combination."
            )
        elif signals:
            status = "two_factor_required"
            reason_code = "ADDITIONAL_VERIFICATION_RECOMMENDED"
            reason_description = (
                "Additional verification is recommended by the experimental antifraud policy."
            )
        else:
            status = "approved"
            reason_code = "RISK_WITHIN_EXPERIMENTAL_POLICY"
            reason_description = "Risk is within the experimental antifraud policy."

        decision_evidence = deepcopy(evidence_snapshot)
        decision_evidence["policy"] = {
            "policy_version": self.config.policy_version,
            "triggered_signals": signals,
        }
        return {
            "status": status,
            "metadata": {
                "schema_version": "1.0",
                "policy_version": self.config.policy_version,
                "reason_code": reason_code,
                "reason_description": reason_description,
            },
            "evidence_snapshot": decision_evidence,
            "policy_version": self.config.policy_version,
        }

    @staticmethod
    def _positive_int(value) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RiskPolicyUnavailable()
        return value

    @staticmethod
    def _metric(evidence_snapshot: dict, window: str, metric: str) -> int:
        try:
            if evidence_snapshot["schema_version"] != "pix-history-v1":
                raise RiskPolicyUnavailable()
            value = evidence_snapshot["windows"][window][metric]
        except (KeyError, TypeError):
            raise RiskPolicyUnavailable()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RiskPolicyUnavailable()
        return value
