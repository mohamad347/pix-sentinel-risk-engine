from copy import deepcopy
from dataclasses import dataclass, fields
import math

from errors import RiskPolicyUnavailable
from utils.pix_context import ADAPTIVE_CONTEXTUAL_EVIDENCE_SCHEMA_VERSION


ADAPTIVE_POLICY_VERSION = "mvp-risk-adaptive-v3-r2"
ADAPTIVE_HISTORY_WINDOWS_MINUTES = (1, 5, 15, 60, 1440)


@dataclass(frozen=True)
class AdaptiveFamilyCaps:
    exposure_velocity: int = 60
    profile: int = 15
    relationship: int = 15
    access: int = 20
    temporal: int = 25
    beneficiary_network: int = 20

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 100:
                raise RiskPolicyUnavailable()


@dataclass(frozen=True)
class AdaptiveRiskConfig:
    policy_version: str = ADAPTIVE_POLICY_VERSION
    two_factor_score: int = 25
    reject_score: int = 60
    profile_min_prior_attempts: int = 5
    profile_min_span_seconds: int = 86_400
    context_min_prior_attempts: int = 3
    geolocation_min_distance_km: int = 100
    elevated_geolocation_speed_kmh: int = 300
    critical_geolocation_speed_kmh: int = 1_000
    simultaneous_geolocation_seconds: int = 60
    convergent_min_families: int = 4
    family_caps: AdaptiveFamilyCaps = AdaptiveFamilyCaps()

    def __post_init__(self) -> None:
        if not isinstance(self.policy_version, str) or not self.policy_version:
            raise RiskPolicyUnavailable()
        for name in (
            "two_factor_score",
            "reject_score",
            "profile_min_prior_attempts",
            "profile_min_span_seconds",
            "context_min_prior_attempts",
            "geolocation_min_distance_km",
            "elevated_geolocation_speed_kmh",
            "critical_geolocation_speed_kmh",
            "simultaneous_geolocation_seconds",
            "convergent_min_families",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RiskPolicyUnavailable()
        if not 0 < self.two_factor_score < self.reject_score <= 100:
            raise RiskPolicyUnavailable()
        if self.elevated_geolocation_speed_kmh >= self.critical_geolocation_speed_kmh:
            raise RiskPolicyUnavailable()
        if self.convergent_min_families > len(fields(AdaptiveFamilyCaps)):
            raise RiskPolicyUnavailable()
        if not isinstance(self.family_caps, AdaptiveFamilyCaps):
            raise RiskPolicyUnavailable()


DEFAULT_ADAPTIVE_RISK_CONFIG = AdaptiveRiskConfig()


class PixAdaptiveRiskPolicy:
    """Adaptive policy with independent, capped signal families.

    Declared or locally observed context may recommend one of two 2FA challenges.
    Rejection is guarded by a critical burst, an impossible geolocation combined
    with access anomalies, or convergence across several capped families. A
    challenge is a recommendation, not proof that authentication was executed.
    """

    requires_contextual_evidence = True
    requires_approved_profile_history = True
    requires_adaptive_contextual_evidence = True
    history_windows_minutes = ADAPTIVE_HISTORY_WINDOWS_MINUTES

    def __init__(self, config: AdaptiveRiskConfig = DEFAULT_ADAPTIVE_RISK_CONFIG) -> None:
        self.config = config

    def evaluate(self, transaction_data: dict, evidence_snapshot: dict) -> dict:
        amount = self._positive_int(transaction_data.get("amount_cents"))
        contextual = self._contextual(evidence_snapshot)
        prior_count = self._non_negative_int(evidence_snapshot.get("prior_attempt_count"))

        exposure, exposure_signals, critical_burst = self._exposure(evidence_snapshot, amount)
        profile, profile_signals, profile_ratio = self._profile(contextual, amount)
        relationship, relationship_signals = self._relationship(
            evidence_snapshot, contextual, prior_count
        )
        access, access_signals = self._access(contextual, prior_count)
        temporal, temporal_signals, critical_geolocation = self._temporal(contextual)
        beneficiary, beneficiary_signals = self._beneficiary(contextual, amount)

        families = {
            "exposure_velocity": exposure,
            "profile": profile,
            "relationship": relationship,
            "access": access,
            "temporal": temporal,
            "beneficiary_network": beneficiary,
        }
        risk_score = min(100, sum(families.values()))
        active_family_count = sum(points > 0 for points in families.values())
        triggered_signals = (
            exposure_signals
            + profile_signals
            + relationship_signals
            + access_signals
            + temporal_signals
            + beneficiary_signals
        )

        critical_geolocation_safeguard = critical_geolocation and access >= 10
        convergent_critical_risk = (
            risk_score >= self.config.reject_score
            and active_family_count >= self.config.convergent_min_families
            and (access >= 18 or beneficiary >= 15)
        )
        rejection_safeguards = []
        if critical_burst:
            rejection_safeguards.append("CRITICAL_BURST_5M")
        if critical_geolocation_safeguard:
            rejection_safeguards.append("CRITICAL_GEO_ACCESS_MISMATCH")
        if convergent_critical_risk:
            rejection_safeguards.append("CONVERGENT_CRITICAL_RISK")

        if critical_burst:
            status = "rejected"
            reason_code = "ADAPTIVE_CRITICAL_BURST"
            reason_description = "A critical short-window burst exceeded the adaptive safeguard."
        elif critical_geolocation_safeguard:
            status = "rejected"
            reason_code = "ADAPTIVE_IMPOSSIBLE_TRAVEL"
            reason_description = "An implausible location change coincided with anomalous access."
        elif convergent_critical_risk:
            status = "rejected"
            reason_code = "ADAPTIVE_CONVERGENT_CRITICAL_RISK"
            reason_description = "Several independent risk contexts exceeded the critical safeguard."
        elif risk_score >= self.config.two_factor_score:
            status = "two_factor_required"
            reason_code = "ADAPTIVE_VERIFICATION_RECOMMENDED"
            reason_description = "Independent risk contexts recommend additional verification."
        else:
            status = "approved"
            reason_code = "ADAPTIVE_RISK_LOW"
            reason_description = "Risk is within the adaptive policy."

        metadata = {
            "schema_version": "1.0",
            "policy_version": self.config.policy_version,
            "reason_code": reason_code,
            "reason_description": reason_description,
        }
        if status == "two_factor_required":
            metadata["challenge"] = self._recommended_challenge(families)

        decision_evidence = deepcopy(evidence_snapshot)
        decision_evidence["policy"] = {
            "policy_version": self.config.policy_version,
            "risk_score": risk_score,
            "families": families,
            "family_caps": {
                field.name: getattr(self.config.family_caps, field.name)
                for field in fields(self.config.family_caps)
            },
            "profile_ratio_basis_points": profile_ratio,
            "critical_burst_safeguard": critical_burst,
            "critical_geolocation_safeguard": critical_geolocation_safeguard,
            "convergent_critical_risk": convergent_critical_risk,
            "active_risk_family_count": active_family_count,
            "rejection_safeguards": rejection_safeguards,
            "triggered_signals": triggered_signals,
        }
        return {
            "status": status,
            "metadata": metadata,
            "evidence_snapshot": decision_evidence,
            "policy_version": self.config.policy_version,
        }

    @staticmethod
    def _recommended_challenge(families: dict[str, int]) -> dict[str, str]:
        identity_or_access_takeover = families["access"] >= 18 and (
            families["profile"] >= 10
            or families["relationship"] >= 12
            or families["temporal"] >= 15
        )
        if identity_or_access_takeover:
            return {
                "type": "facial_liveness",
                "level": "enhanced",
                "status": "recommended",
            }
        return {
            "type": "push_confirmation",
            "level": "standard",
            "status": "recommended",
        }

    def _exposure(self, evidence: dict, amount: int) -> tuple[int, list[str], bool]:
        windows = {
            name: {
                "count": self._metric(evidence, name, "attempt_count"),
                "amount": self._metric(evidence, name, "amount_cents"),
            }
            for name in ("1m", "5m", "15m", "60m", "1440m")
        }
        signals = []
        points = 0
        if amount >= 500_000:
            signals.append("ELEVATED_CURRENT_VALUE")
            points = 25
        if windows["1m"]["count"] >= 4:
            signals.append("MICROBURST_1M")
            points = max(points, 25)
        if windows["5m"]["count"] >= 4:
            signals.append("ELEVATED_ATTEMPT_COUNT_5M")
            points = max(points, 25)
        if windows["60m"]["amount"] >= 1_500_000:
            signals.append("ELEVATED_ATTEMPT_TOTAL_60M")
            points = max(points, 25)
        if windows["1440m"]["amount"] >= 5_000_000:
            signals.append("ELEVATED_ATTEMPT_TOTAL_24H")
            points = max(points, 25)
        if windows["60m"]["count"] >= 10 and windows["60m"]["amount"] >= 1_500_000:
            signals.append("STRUCTURED_AGGREGATE_60M")
            points = max(points, 25)

        critical_burst = (
            windows["5m"]["count"] >= 8 and windows["5m"]["amount"] >= 3_000_000
        )
        if critical_burst:
            signals.append("CRITICAL_BURST_5M")
            points = self.config.family_caps.exposure_velocity
        return min(points, self.config.family_caps.exposure_velocity), signals, critical_burst

    def _profile(self, contextual: dict, amount: int) -> tuple[int, list[str], int | None]:
        profile = self._mapping(contextual, "profile")
        if profile.get("observation") not in {"observed", "insufficient_history"}:
            raise RiskPolicyUnavailable()
        count = self._non_negative_int(profile.get("prior_attempt_count"))
        span = self._non_negative_int(profile.get("history_span_seconds"))
        if (
            profile.get("observation") != "observed"
            or count < self.config.profile_min_prior_attempts
            or span < self.config.profile_min_span_seconds
        ):
            return 0, [], None

        median = self._positive_int(profile.get("median_amount_cents"))
        p90 = self._positive_int(profile.get("p90_amount_cents"))
        ratio = amount * 10_000 // median
        signals = []
        points = 0
        if amount >= p90 * 2:
            signals.append("ABOVE_PROFILE_P90")
            points = 10
        if ratio >= 80_000:
            signals.append("CRITICAL_MEDIAN_DEVIATION")
            points = self.config.family_caps.profile
        elif ratio >= 30_000:
            signals.append("ELEVATED_MEDIAN_DEVIATION")
            points = max(points, 10)
        return min(points, self.config.family_caps.profile), signals, ratio

    def _relationship(self, evidence: dict, contextual: dict, prior_count: int):
        observation = self._mapping(evidence, "counterparty").get("observation")
        if observation not in {"observed", "not_observed", "insufficient_history"}:
            raise RiskPolicyUnavailable()
        relationship = self._mapping(contextual, "relationship")
        pair_count = self._non_negative_int(relationship.get("pair_attempt_count_15m"))
        distinct = self._non_negative_int(
            relationship.get("distinct_counterparty_count_60m")
        )
        self._non_negative_int(relationship.get("prior_counterparty_attempt_count"))
        signals = []
        points = 0
        if prior_count >= self.config.context_min_prior_attempts and observation == "not_observed":
            signals.append("NEW_COUNTERPARTY")
            points = 5
        if pair_count >= 5:
            signals.append("PAIR_REPETITION_15M")
            points = max(points, 10)
        if distinct >= 8:
            signals.append("CRITICAL_FAN_OUT_60M")
            points = self.config.family_caps.relationship
        elif distinct >= 4:
            signals.append("ELEVATED_FAN_OUT_60M")
            points = max(points, 12)
        return min(points, self.config.family_caps.relationship), signals

    def _access(self, contextual: dict, prior_count: int):
        access = self._mapping(contextual, "access")
        allowed = {
            "device_observation": {"observed", "newly_observed", "insufficient_history"},
            "ip_observation": {"observed", "newly_observed", "missing", "insufficient_history"},
            "channel_observation": {"observed", "changed", "missing", "insufficient_history"},
        }
        for key, states in allowed.items():
            if access.get(key) not in states:
                raise RiskPolicyUnavailable()
        devices = self._non_negative_int(access.get("distinct_device_count_60m"))
        ips = self._non_negative_int(access.get("distinct_ip_count_60m"))
        device_payers = self._non_negative_int(
            access.get("device_distinct_payer_count_60m")
        )
        ip_payers = self._non_negative_int(access.get("ip_distinct_payer_count_60m"))

        signals = []
        points = 0
        if prior_count >= self.config.context_min_prior_attempts:
            if access["device_observation"] == "newly_observed":
                signals.append("NEW_DEVICE")
                points += 10
            if access["ip_observation"] == "newly_observed":
                signals.append("NEW_IP")
                points += 8
            if access["channel_observation"] == "changed":
                signals.append("CHANNEL_CHANGE")
                points += 5
            if devices >= 3:
                signals.append("DEVICE_CHURN_60M")
                points = max(points, 15)
            if ips >= 3:
                signals.append("IP_CHURN_60M")
                points = max(points, 12)
        if device_payers >= 3:
            signals.append("DEVICE_SHARED_ACROSS_PAYERS_60M")
            points = max(points, 15)
        if ip_payers >= 5:
            signals.append("IP_SHARED_ACROSS_PAYERS_60M")
            points = max(points, 8)
        return min(points, self.config.family_caps.access), signals

    def _temporal(self, contextual: dict):
        temporal = self._mapping(contextual, "temporal")
        skew = temporal.get("transaction_clock_skew_seconds")
        if isinstance(skew, bool) or not isinstance(skew, int):
            raise RiskPolicyUnavailable()
        if temporal.get("nighttime_observation") not in {
            "habitual",
            "unusual",
            "not_nighttime",
            "insufficient_history",
        }:
            raise RiskPolicyUnavailable()
        geo = self._mapping(temporal, "geolocation")
        if geo.get("observation") not in {
            "missing",
            "insufficient_history",
            "observed",
            "invalid",
        }:
            raise RiskPolicyUnavailable()

        signals = []
        points = 0
        critical_geolocation = False
        if abs(skew) >= 7_200:
            signals.append("TRANSACTION_CLOCK_SKEW")
            points = 8
        if temporal["nighttime_observation"] == "unusual":
            signals.append("UNUSUAL_NIGHTTIME")
            points = max(points, 5)
        if geo.get("observation") == "observed":
            distance = geo.get("distance_km")
            elapsed = geo.get("elapsed_seconds")
            speed = geo.get("speed_kmh")
            if (
                speed is None
                and isinstance(distance, (int, float))
                and not isinstance(distance, bool)
                and isinstance(elapsed, int)
                and not isinstance(elapsed, bool)
                and elapsed > 0
            ):
                # Backward-compatible derivation for evidence created before
                # speed_kmh became an explicit privacy-minimized feature.
                speed = distance * 3_600 / elapsed
            if (
                isinstance(distance, bool)
                or not isinstance(distance, (int, float))
                or not math.isfinite(distance)
                or distance < 0
                or isinstance(elapsed, bool)
                or not isinstance(elapsed, int)
                or elapsed < 0
                or (
                    speed is not None
                    and (
                        isinstance(speed, bool)
                        or not isinstance(speed, (int, float))
                        or not math.isfinite(speed)
                        or speed < 0
                    )
                )
                or (elapsed == 0 and speed is not None)
                or (elapsed > 0 and speed is None)
            ):
                raise RiskPolicyUnavailable()

            relevant_distance = distance >= self.config.geolocation_min_distance_km
            simultaneous_distant = (
                relevant_distance
                and elapsed <= self.config.simultaneous_geolocation_seconds
            )
            impossible_velocity = (
                relevant_distance
                and speed is not None
                and speed > self.config.critical_geolocation_speed_kmh
            )
            if simultaneous_distant or impossible_velocity:
                signals.append("IMPOSSIBLE_GEO_VELOCITY")
                points = self.config.family_caps.temporal
                critical_geolocation = True
            elif (
                relevant_distance
                and speed is not None
                and speed >= self.config.elevated_geolocation_speed_kmh
            ):
                signals.append("ELEVATED_GEO_VELOCITY")
                points = max(points, 10)
        return (
            min(points, self.config.family_caps.temporal),
            signals,
            critical_geolocation,
        )

    def _beneficiary(self, contextual: dict, amount: int):
        beneficiary = self._mapping(contextual, "beneficiary")
        if beneficiary.get("global_observation") not in {
            "observed",
            "not_observed",
            "insufficient_history",
        }:
            raise RiskPolicyUnavailable()
        payers = self._non_negative_int(beneficiary.get("distinct_payer_count_60m"))
        attempts = self._non_negative_int(beneficiary.get("attempt_count_60m"))
        age = beneficiary.get("destination_account_age_days")
        if isinstance(age, bool) or not isinstance(age, int):
            raise RiskPolicyUnavailable()

        signals = []
        points = 0
        if beneficiary["global_observation"] == "not_observed":
            signals.append("RECIPIENT_FIRST_SYSTEM_OBSERVATION")
            points = 5
        if payers >= 5 and attempts >= 8:
            signals.append("RECIPIENT_FAN_IN_60M")
            points = 15
        if 0 <= age < 7 and amount >= 300_000:
            signals.append("YOUNG_DESTINATION_ACCOUNT")
            points += 5
        return min(points, self.config.family_caps.beneficiary_network), signals

    @staticmethod
    def _contextual(evidence: dict) -> dict:
        contextual = PixAdaptiveRiskPolicy._mapping(evidence, "contextual")
        if contextual.get("schema_version") != ADAPTIVE_CONTEXTUAL_EVIDENCE_SCHEMA_VERSION:
            raise RiskPolicyUnavailable()
        return contextual

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
        return PixAdaptiveRiskPolicy._non_negative_int(value)

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
