from copy import deepcopy
from dataclasses import dataclass, fields, replace

from errors import RiskPolicyUnavailable
from utils.pix_context import CONTEXTUAL_EVIDENCE_SCHEMA_VERSION


CONTEXTUAL_POLICY_VERSION = "mvp-risk-contextual-v2-candidate"
CONTEXTUAL_POLICY_VERSION_R2 = "mvp-risk-contextual-v2-candidate-r2"


@dataclass(frozen=True)
class ContextualScoreWeights:
    value_signal: int = 15
    value_high: int = 25
    velocity_signal: int = 20
    velocity_critical: int = 30
    aggregate_signal: int = 20
    aggregate_critical: int = 30
    critical_combination_bonus: int = 15
    exposure_cap: int = 45
    profile_elevated: int = 15
    profile_critical: int = 25
    counterparty_novelty: int = 5
    diversity_elevated: int = 8
    diversity_critical: int = 15
    device_new: int = 10
    device_with_velocity: int = 15

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 100:
                raise RiskPolicyUnavailable()
        if self.value_high < self.value_signal:
            raise RiskPolicyUnavailable()
        if self.velocity_critical < self.velocity_signal:
            raise RiskPolicyUnavailable()
        if self.aggregate_critical < self.aggregate_signal:
            raise RiskPolicyUnavailable()
        if self.exposure_cap < max(
            self.value_high,
            self.velocity_critical,
            self.aggregate_critical,
        ):
            raise RiskPolicyUnavailable()
        if self.profile_critical < self.profile_elevated:
            raise RiskPolicyUnavailable()
        if self.diversity_critical < self.diversity_elevated:
            raise RiskPolicyUnavailable()
        if self.device_with_velocity < self.device_new:
            raise RiskPolicyUnavailable()


@dataclass(frozen=True)
class ContextualRiskConfig:
    policy_version: str
    two_factor_score: int
    reject_score: int
    value_2fa_cents: int
    value_high_cents: int
    count_2fa_5m: int
    count_critical_5m: int
    total_2fa_60m_cents: int
    total_critical_5m_cents: int
    profile_min_prior_attempts: int
    profile_min_span_seconds: int
    profile_elevated_ratio_basis_points: int
    profile_critical_ratio_basis_points: int
    context_min_prior_attempts: int
    weights: ContextualScoreWeights = ContextualScoreWeights()
    contextual_families_enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise RiskPolicyUnavailable()
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in {"policy_version", "weights", "contextual_families_enabled"}:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RiskPolicyUnavailable()
        if not isinstance(self.contextual_families_enabled, bool):
            raise RiskPolicyUnavailable()
        if not isinstance(self.weights, ContextualScoreWeights):
            raise RiskPolicyUnavailable()
        if not 0 < self.two_factor_score < self.reject_score <= 100:
            raise RiskPolicyUnavailable()
        if self.value_high_cents < self.value_2fa_cents:
            raise RiskPolicyUnavailable()
        if self.count_critical_5m < self.count_2fa_5m:
            raise RiskPolicyUnavailable()
        if self.profile_critical_ratio_basis_points < self.profile_elevated_ratio_basis_points:
            raise RiskPolicyUnavailable()

    @classmethod
    def ablation(cls) -> "ContextualRiskConfig":
        return replace(
            DEFAULT_CONTEXTUAL_RISK_CONFIG,
            policy_version="mvp-risk-contextual-v2-ablation",
            contextual_families_enabled=False,
        )


DEFAULT_CONTEXTUAL_RISK_CONFIG = ContextualRiskConfig(
    policy_version=CONTEXTUAL_POLICY_VERSION,
    two_factor_score=30,
    reject_score=70,
    value_2fa_cents=500_000,
    value_high_cents=1_500_000,
    count_2fa_5m=4,
    count_critical_5m=8,
    total_2fa_60m_cents=1_500_000,
    total_critical_5m_cents=3_000_000,
    profile_min_prior_attempts=5,
    profile_min_span_seconds=86_400,
    profile_elevated_ratio_basis_points=30_000,
    profile_critical_ratio_basis_points=80_000,
    context_min_prior_attempts=3,
)


REVISED_CONTEXTUAL_RISK_CONFIG = ContextualRiskConfig(
    policy_version=CONTEXTUAL_POLICY_VERSION_R2,
    two_factor_score=25,
    reject_score=60,
    value_2fa_cents=500_000,
    value_high_cents=1_500_000,
    count_2fa_5m=4,
    count_critical_5m=8,
    total_2fa_60m_cents=1_500_000,
    total_critical_5m_cents=3_000_000,
    profile_min_prior_attempts=5,
    profile_min_span_seconds=86_400,
    profile_elevated_ratio_basis_points=30_000,
    profile_critical_ratio_basis_points=80_000,
    context_min_prior_attempts=3,
    weights=ContextualScoreWeights(
        value_signal=25,
        value_high=25,
        velocity_signal=25,
        velocity_critical=25,
        aggregate_signal=25,
        aggregate_critical=25,
        critical_combination_bonus=35,
        exposure_cap=60,
        profile_elevated=10,
        profile_critical=15,
        counterparty_novelty=5,
        diversity_elevated=8,
        diversity_critical=15,
        device_new=10,
        device_with_velocity=15,
    ),
)


class PixContextualRiskPolicy:
    requires_contextual_evidence = True

    def __init__(
        self,
        config: ContextualRiskConfig = DEFAULT_CONTEXTUAL_RISK_CONFIG,
    ) -> None:
        self.config = config

    @property
    def requires_approved_profile_history(self) -> bool:
        return self.config.policy_version == CONTEXTUAL_POLICY_VERSION_R2

    def evaluate(self, transaction_data: dict, evidence_snapshot: dict) -> dict:
        amount_cents = self._positive_int(transaction_data.get("amount_cents"))
        count_5m = self._metric(evidence_snapshot, "5m", "attempt_count")
        total_5m = self._metric(evidence_snapshot, "5m", "amount_cents")
        total_60m = self._metric(evidence_snapshot, "60m", "amount_cents")
        contextual = self._contextual(evidence_snapshot)
        self._validate_diagnostics(contextual)
        prior_count = self._non_negative_int(evidence_snapshot.get("prior_attempt_count"))

        exposure_points, exposure_signals, critical_burst = self._exposure_points(
            amount_cents,
            count_5m,
            total_5m,
            total_60m,
        )
        profile_points, profile_ratio = self._profile_points(amount_cents, contextual)
        counterparty_points = self._counterparty_points(
            evidence_snapshot,
            contextual,
            prior_count,
        )
        device_points = self._device_points(contextual, prior_count, count_5m)

        if not self.config.contextual_families_enabled:
            profile_points = 0
            counterparty_points = 0
            device_points = 0
            profile_ratio = None

        families = {
            "exposure_velocity": exposure_points,
            "profile": profile_points,
            "counterparty_diversity": counterparty_points,
            "device": device_points,
            "geolocation": 0,
        }
        risk_score = min(100, sum(families.values()))

        if risk_score >= self.config.reject_score and critical_burst:
            status = "rejected"
            reason_code = "CONTEXTUAL_CRITICAL_COMBINATION"
            reason_description = "The request matched a contextual critical-risk combination."
        elif risk_score >= self.config.two_factor_score:
            status = "two_factor_required"
            reason_code = "CONTEXTUAL_VERIFICATION_RECOMMENDED"
            reason_description = "Additional verification is recommended by the contextual policy."
        else:
            status = "approved"
            reason_code = "CONTEXTUAL_RISK_LOW"
            reason_description = "Risk is within the contextual candidate policy."

        decision_evidence = deepcopy(evidence_snapshot)
        decision_evidence["policy"] = {
            "policy_version": self.config.policy_version,
            "risk_score": risk_score,
            "families": families,
            "profile_ratio_basis_points": profile_ratio,
            "critical_burst_safeguard": critical_burst,
            "triggered_signals": exposure_signals
            + self._contextual_signal_names(
                profile_points,
                counterparty_points,
                device_points,
            ),
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

    def _exposure_points(self, amount, count_5m, total_5m, total_60m):
        signals = []
        if amount >= self.config.value_high_cents:
            value_points = self.config.weights.value_high
            signals.append("ELEVATED_CURRENT_VALUE")
        elif amount >= self.config.value_2fa_cents:
            value_points = self.config.weights.value_signal
            signals.append("CURRENT_VALUE_SIGNAL")
        else:
            value_points = 0

        if count_5m >= self.config.count_critical_5m:
            count_points = self.config.weights.velocity_critical
            signals.append("CRITICAL_ATTEMPT_COUNT_5M")
        elif count_5m >= self.config.count_2fa_5m:
            count_points = self.config.weights.velocity_signal
            signals.append("ELEVATED_ATTEMPT_COUNT_5M")
        else:
            count_points = 0

        if total_5m >= self.config.total_critical_5m_cents:
            total_points = self.config.weights.aggregate_critical
            signals.append("CRITICAL_ATTEMPT_TOTAL_5M")
        elif total_60m >= self.config.total_2fa_60m_cents:
            total_points = self.config.weights.aggregate_signal
            signals.append("ELEVATED_ATTEMPT_TOTAL_60M")
        else:
            total_points = 0

        critical_burst = (
            count_5m >= self.config.count_critical_5m
            and total_5m >= self.config.total_critical_5m_cents
        )
        points = max(value_points, count_points, total_points)
        if critical_burst:
            points = min(
                self.config.weights.exposure_cap,
                points + self.config.weights.critical_combination_bonus,
            )
        return points, signals, critical_burst

    def _profile_points(self, amount_cents: int, contextual: dict) -> tuple[int, int | None]:
        profile = self._mapping(contextual, "profile")
        observation = profile.get("observation")
        if observation not in {"observed", "insufficient_history"}:
            raise RiskPolicyUnavailable()
        if observation != "observed":
            return 0, None

        count = self._non_negative_int(profile.get("prior_attempt_count"))
        span = self._non_negative_int(profile.get("history_span_seconds"))
        median = profile.get("median_amount_cents")
        if count < self.config.profile_min_prior_attempts or span < self.config.profile_min_span_seconds:
            return 0, None
        median = self._positive_int(median)
        ratio = amount_cents * 10_000 // median
        if ratio >= self.config.profile_critical_ratio_basis_points:
            return self.config.weights.profile_critical, ratio
        if ratio >= self.config.profile_elevated_ratio_basis_points:
            return self.config.weights.profile_elevated, ratio
        return 0, ratio

    def _counterparty_points(self, evidence: dict, contextual: dict, prior_count: int) -> int:
        counterparty = self._mapping(evidence, "counterparty")
        observation = counterparty.get("observation")
        if observation not in {"observed", "not_observed", "insufficient_history"}:
            raise RiskPolicyUnavailable()
        novelty_points = (
            self.config.weights.counterparty_novelty
            if prior_count >= self.config.context_min_prior_attempts
            and observation == "not_observed"
            else 0
        )
        diversity = self._mapping(self._mapping(contextual, "diversity"), "60m")
        distinct_count = self._non_negative_int(diversity.get("distinct_counterparty_count"))
        if distinct_count >= 8:
            diversity_points = self.config.weights.diversity_critical
        elif distinct_count >= 4:
            diversity_points = self.config.weights.diversity_elevated
        else:
            diversity_points = 0
        return max(novelty_points, diversity_points)

    def _device_points(self, contextual: dict, prior_count: int, count_5m: int) -> int:
        device = self._mapping(contextual, "device")
        observation = device.get("observation")
        if observation not in {"observed", "newly_observed", "insufficient_history"}:
            raise RiskPolicyUnavailable()
        self._non_negative_int(device.get("prior_attempt_count"))
        if (
            prior_count < self.config.context_min_prior_attempts
            or observation != "newly_observed"
        ):
            return 0
        return (
            self.config.weights.device_with_velocity
            if count_5m >= self.config.count_2fa_5m
            else self.config.weights.device_new
        )

    @staticmethod
    def _contextual_signal_names(profile: int, counterparty: int, device: int) -> list[str]:
        signals = []
        if profile:
            signals.append("HISTORICAL_VALUE_DEVIATION")
        if counterparty:
            signals.append("COUNTERPARTY_OR_DIVERSITY_CONTEXT")
        if device:
            signals.append("NEWLY_OBSERVED_DEVICE_CONTEXT")
        return signals

    @staticmethod
    def _contextual(evidence: dict) -> dict:
        contextual = PixContextualRiskPolicy._mapping(evidence, "contextual")
        if contextual.get("schema_version") != CONTEXTUAL_EVIDENCE_SCHEMA_VERSION:
            raise RiskPolicyUnavailable()
        return contextual

    @staticmethod
    def _validate_diagnostics(contextual: dict) -> None:
        geolocation = PixContextualRiskPolicy._mapping(contextual, "geolocation")
        if geolocation.get("observation") not in {
            "missing",
            "insufficient_history",
            "observed",
            "invalid",
        }:
            raise RiskPolicyUnavailable()
        availability = PixContextualRiskPolicy._mapping(
            contextual,
            "source_availability",
        )
        if availability.get("channel") not in {"observed", "missing"}:
            raise RiskPolicyUnavailable()
        if availability.get("ip") not in {"observed", "missing"}:
            raise RiskPolicyUnavailable()
        party_types = PixContextualRiskPolicy._mapping(contextual, "party_types")
        if party_types.get("payer") not in {"pf", "pj", "unknown"}:
            raise RiskPolicyUnavailable()
        if party_types.get("payee") not in {"pf", "pj", "unknown"}:
            raise RiskPolicyUnavailable()

    @staticmethod
    def _mapping(value: dict, key: str) -> dict:
        try:
            result = value[key]
        except (KeyError, TypeError):
            raise RiskPolicyUnavailable()
        if not isinstance(result, dict):
            raise RiskPolicyUnavailable()
        return result

    @staticmethod
    def _metric(evidence: dict, window: str, metric: str) -> int:
        try:
            if evidence["schema_version"] != "pix-history-v1":
                raise RiskPolicyUnavailable()
            value = evidence["windows"][window][metric]
        except (KeyError, TypeError):
            raise RiskPolicyUnavailable()
        return PixContextualRiskPolicy._non_negative_int(value)

    @staticmethod
    def _positive_int(value) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RiskPolicyUnavailable()
        return value

    @staticmethod
    def _non_negative_int(value) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RiskPolicyUnavailable()
        return value
