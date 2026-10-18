"""Executa o experimento sintético V1 × V2 × ablação com seed fixa."""

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://offline:offline@localhost/offline_not_connected",
)
os.environ.setdefault("INTERNAL_TOKEN", "offline-not-used")

from controllers.pix_contextual_risk_policy import (  # noqa: E402
    ContextualRiskConfig,
    REVISED_CONTEXTUAL_RISK_CONFIG,
    PixContextualRiskPolicy,
)
from controllers.pix_adaptive_risk_policy import PixAdaptiveRiskPolicy  # noqa: E402
from controllers.pix_risk_policy import PixRiskPolicy  # noqa: E402


SEED = 20261018
LABEL_TO_STATUS = {
    "legitimo": "approved",
    "duvida": "two_factor_required",
    "critico": "rejected",
}


@dataclass(frozen=True)
class Scenario:
    id: str
    experiment: str
    label: str
    description: str
    amount: int = 10_000
    count_5m: int = 1
    amount_5m: int = 10_000
    amount_60m: int = 10_000
    prior_count: int = 0
    counterparty: str = "insufficient_history"
    device: str = "insufficient_history"
    device_count: int = 0
    profile: str = "insufficient_history"
    profile_count: int = 0
    profile_span: int = 0
    median: int | None = None
    diversity: int = 1
    geo: str = "missing"
    payer_type: str = "pf"
    payee_type: str = "pf"


def scenarios() -> list[Scenario]:
    # B2 is the development set. C2 is declared independently below and its
    # results must not be used to alter this candidate configuration.
    b2 = [
        Scenario("B2-L1", "B2", "legitimo", "cold start de baixo valor"),
        Scenario("B2-L2", "B2", "legitimo", "alto valor único legítimo", 500_000, amount_5m=500_000, amount_60m=500_000),
        Scenario("B2-L3", "B2", "legitimo", "PJ com quatro pagamentos recorrentes", 10_000, 4, 40_000, 40_000, 3, "observed", "observed", 3, diversity=1, payer_type="pj", payee_type="pj"),
        Scenario("B2-D1", "B2", "duvida", "aumento súbito frente ao perfil", 500_000, amount_5m=500_000, amount_60m=500_000, prior_count=5, counterparty="observed", device="observed", device_count=5, profile="observed", profile_count=5, profile_span=86_400, median=50_000, payer_type="pf", payee_type="pj"),
        Scenario("B2-D2", "B2", "duvida", "novo dispositivo com velocity e dispersão", 10_000, 4, 40_000, 40_000, 3, "not_observed", "newly_observed", diversity=4),
        Scenario("B2-D3", "B2", "duvida", "montante agregado em 60 minutos", 300_000, 3, 300_000, 1_500_000, 5, "observed", "observed", 5, diversity=1),
        Scenario("B2-C1", "B2", "critico", "rajada crítica diversa e dispositivo novo", 375_000, 8, 3_000_000, 3_000_000, 7, "not_observed", "newly_observed", profile="observed", profile_count=7, profile_span=86_400, median=40_000, diversity=8),
        Scenario("B2-C2", "B2", "critico", "salto extremo sem rajada crítica", 900_000, 2, 910_000, 910_000, 6, "not_observed", "newly_observed", profile="observed", profile_count=6, profile_span=172_800, median=50_000, diversity=2),
        Scenario("B2-C3", "B2", "critico", "muitas tentativas pequenas dispersas", 10_000, 8, 80_000, 80_000, 7, "not_observed", "newly_observed", diversity=8),
    ]
    c2 = [
        Scenario("C2-L1", "C2", "legitimo", "Pix rotineiro para contraparte observada", prior_count=8, counterparty="observed", device="observed", device_count=8, profile="observed", profile_count=8, profile_span=604_800, median=10_000),
        Scenario("C2-L2", "C2", "legitimo", "PJ paga quatro vezes o mesmo fornecedor", 20_000, 4, 80_000, 80_000, 12, "observed", "observed", 12, "observed", 12, 604_800, 20_000, 1, payer_type="pj", payee_type="pj"),
        Scenario("C2-L3", "C2", "legitimo", "transferência pontual de R$ 30 mil", 3_000_000, amount_5m=3_000_000, amount_60m=3_000_000),
        Scenario("C2-D1", "C2", "duvida", "novo dispositivo durante quatro tentativas", 50_000, 4, 200_000, 200_000, 5, "not_observed", "newly_observed", profile="observed", profile_count=5, profile_span=172_800, median=50_000, diversity=4, payer_type="pf", payee_type="pj"),
        Scenario("C2-D2", "C2", "duvida", "salto de perfil abaixo do valor V1", 400_000, amount_5m=400_000, amount_60m=400_000, prior_count=6, counterparty="not_observed", device="newly_observed", profile="observed", profile_count=6, profile_span=172_800, median=100_000, diversity=2, payer_type="pj", payee_type="pf"),
        Scenario("C2-D3", "C2", "duvida", "geos distantes com fonte declarada", prior_count=4, counterparty="observed", device="observed", device_count=4, profile="observed", profile_count=4, profile_span=86_400, median=10_000, geo="observed"),
        Scenario("C2-C1", "C2", "critico", "rajada crítica para contraparte e device observados", 375_000, 8, 3_000_000, 3_000_000, 20, "observed", "observed", 20, "observed", 20, 604_800, 375_000, 1),
        Scenario("C2-C2", "C2", "critico", "rajada crítica dispersa e dispositivo novo", 375_000, 8, 3_000_000, 3_000_000, 7, "not_observed", "newly_observed", profile="observed", profile_count=7, profile_span=172_800, median=50_000, diversity=8),
        Scenario("C2-C3", "C2", "critico", "exposição agregada, perfil e device sem rajada", 600_000, 3, 1_200_000, 1_800_000, 9, "not_observed", "newly_observed", profile="observed", profile_count=9, profile_span=604_800, median=60_000, diversity=3),
    ]
    # C3 was defined after the R2 parameters were frozen. It is intentionally
    # heterogeneous and must be evaluated once, without recalibration.
    c3 = [
        Scenario("C3-L1", "C3", "legitimo", "PF cold start de baixo valor", 25_000, amount_5m=25_000, amount_60m=25_000),
        Scenario("C3-L2", "C3", "legitimo", "PF recorrente em dispositivo e destino observados", 80_000, 2, 160_000, 160_000, 10, "observed", "observed", 10, "observed", 8, 1_209_600, 75_000),
        Scenario("C3-L3", "C3", "legitimo", "PJ em lote crÃ­tico para fornecedor conhecido", 400_000, 8, 3_200_000, 3_200_000, 30, "observed", "observed", 30, "observed", 20, 2_592_000, 400_000, payer_type="pj", payee_type="pj"),
        Scenario("C3-L4", "C3", "legitimo", "pagamento pontual de alto valor", 2_000_000, amount_5m=2_000_000, amount_60m=2_000_000, payer_type="pj", payee_type="pj"),
        Scenario("C3-D1", "C3", "duvida", "novo device e novo destino abaixo dos limites V1", 350_000, 2, 360_000, 360_000, 7, "not_observed", "newly_observed", profile="observed", profile_count=5, profile_span=604_800, median=100_000, diversity=2),
        Scenario("C3-D2", "C3", "duvida", "quarto Pix pequeno disperso em novo device", 40_000, 4, 160_000, 160_000, 3, "not_observed", "newly_observed", diversity=4),
        Scenario("C3-D3", "C3", "duvida", "R$ 5 mil em cold start", 500_000, amount_5m=500_000, amount_60m=500_000),
        Scenario("C3-D4", "C3", "duvida", "desvio de perfil isolado sem contexto novo", 300_000, amount_5m=300_000, amount_60m=300_000, prior_count=9, counterparty="observed", device="observed", device_count=9, profile="observed", profile_count=7, profile_span=1_209_600, median=60_000),
        Scenario("C3-C1", "C3", "critico", "rajada crÃ­tica familiar PF", 375_000, 8, 3_000_000, 3_000_000, 25, "observed", "observed", 25, "observed", 12, 1_209_600, 375_000),
        Scenario("C3-C2", "C3", "critico", "rajada crÃ­tica nova e dispersa", 500_000, 9, 4_500_000, 4_500_000, 8, "not_observed", "newly_observed", profile="observed", profile_count=6, profile_span=604_800, median=50_000, diversity=9),
        Scenario("C3-C3", "C3", "critico", "muitas tentativas pequenas sem volume crÃ­tico", 20_000, 10, 200_000, 200_000, 9, "not_observed", "newly_observed", diversity=10),
        Scenario("C3-C4", "C3", "critico", "salto extremo de perfil sem rajada", 1_000_000, 2, 1_010_000, 1_010_000, 12, "not_observed", "newly_observed", profile="observed", profile_count=8, profile_span=1_209_600, median=40_000, diversity=2),
    ]
    return b2 + c2 + c3


def evidence(case: Scenario) -> dict:
    return {
        "schema_version": "pix-history-v1",
        "admitted_at": "2026-10-18T15:00:00.000000Z",
        "prior_attempt_count": case.prior_count,
        "is_first_observed_attempt": case.prior_count == 0,
        "counterparty": {
            "schema_version": "pix-counterparty-v1",
            "prior_attempt_count": 1 if case.counterparty == "observed" else 0,
            "observation": case.counterparty,
            "is_previously_observed": case.counterparty == "observed",
            "is_unobserved_with_payer_history": case.counterparty == "not_observed",
        },
        "windows": {
            "5m": {"attempt_count": case.count_5m, "amount_cents": case.amount_5m},
            "60m": {"attempt_count": case.count_5m, "amount_cents": case.amount_60m},
        },
        "contextual": {
            "schema_version": "pix-context-v2-candidate-v1",
            "device": {"observation": case.device, "prior_attempt_count": case.device_count},
            "profile": {
                "observation": case.profile,
                "prior_attempt_count": case.profile_count,
                "history_span_seconds": case.profile_span,
                "median_amount_cents": case.median,
            },
            "diversity": {"60m": {"distinct_counterparty_count": case.diversity}},
            "geolocation": {"observation": case.geo},
            "source_availability": {"channel": "observed", "ip": "observed"},
            "party_types": {"payer": case.payer_type, "payee": case.payee_type},
        },
    }


def adaptive_evidence(case: Scenario) -> dict:
    """Project the frozen legacy scenarios into the richer V3 evidence schema.

    The old datasets do not contain IP, channel, fan-in or temporal anomalies, so
    those dimensions stay neutral. This comparison is retrospective rather than
    an independent V3 holdout.
    """
    snapshot = evidence(case)
    snapshot["windows"] = {
        "1m": {
            "attempt_count": min(case.count_5m, 4),
            "amount_cents": min(case.amount_5m, case.amount * 4),
        },
        "5m": {"attempt_count": case.count_5m, "amount_cents": case.amount_5m},
        "15m": {"attempt_count": case.count_5m, "amount_cents": case.amount_5m},
        "60m": {"attempt_count": case.count_5m, "amount_cents": case.amount_60m},
        "1440m": {"attempt_count": case.count_5m, "amount_cents": case.amount_60m},
    }
    snapshot["contextual"] = {
        "schema_version": "pix-context-v3-r4-v4",
        "profile": {
            "observation": case.profile,
            "prior_attempt_count": case.profile_count,
            "history_span_seconds": case.profile_span,
            "median_amount_cents": case.median,
            "p90_amount_cents": case.median * 2 if case.median else None,
        },
        "relationship": {
            "prior_counterparty_attempt_count": (
                1 if case.counterparty == "observed" else 0
            ),
            "pair_attempt_count_15m": 1,
            "distinct_counterparty_count_60m": case.diversity,
        },
        "access": {
            "device_observation": case.device,
            "ip_observation": (
                "insufficient_history" if case.prior_count == 0 else "observed"
            ),
            "channel_observation": (
                "insufficient_history" if case.prior_count == 0 else "observed"
            ),
            "distinct_device_count_60m": 1,
            "distinct_ip_count_60m": 1,
            "device_distinct_payer_count_60m": 1,
            "ip_distinct_payer_count_60m": 1,
        },
        "temporal": {
            "transaction_clock_skew_seconds": 0,
            "nighttime_observation": "habitual",
            "geolocation": (
                {
                    "observation": "observed",
                    "distance_km": 0.0,
                    "elapsed_seconds": 3_600,
                    "speed_kmh": 0.0,
                }
                if case.geo == "observed"
                else {"observation": case.geo}
            ),
        },
        "beneficiary": {
            "global_observation": (
                "insufficient_history" if case.prior_count == 0 else "observed"
            ),
            "distinct_payer_count_60m": 1,
            "attempt_count_60m": 1,
            "destination_account_age_days": 365,
        },
        "party_types": {"payer": case.payer_type, "payee": case.payee_type},
        "account_context": {
            "source_account_age_days": 365,
            "is_self_transfer": False,
        },
    }
    return snapshot


def run() -> dict:
    random.seed(SEED)
    v1 = PixRiskPolicy()
    v2 = PixContextualRiskPolicy()
    v2_r2 = PixContextualRiskPolicy(REVISED_CONTEXTUAL_RISK_CONFIG)
    v3 = PixAdaptiveRiskPolicy()
    ablation = PixContextualRiskPolicy(ContextualRiskConfig.ablation())
    results = []
    for case in scenarios():
        snapshot = evidence(case)
        transaction = {"amount_cents": case.amount}
        v1_result = v1.evaluate(transaction, snapshot)
        v2_result = v2.evaluate(transaction, snapshot)
        v2_r2_result = v2_r2.evaluate(transaction, snapshot)
        v3_result = v3.evaluate(transaction, adaptive_evidence(case))
        ablation_result = ablation.evaluate(transaction, snapshot)
        policy = v2_result["evidence_snapshot"]["policy"]
        policy_r2 = v2_r2_result["evidence_snapshot"]["policy"]
        policy_v3 = v3_result["evidence_snapshot"]["policy"]
        results.append(
            {
                "id": case.id,
                "experiment": case.experiment,
                "synthetic_label": case.label,
                "expected_status": LABEL_TO_STATUS[case.label],
                "description": case.description,
                "inputs": {
                    "amount_cents": case.amount,
                    "attempt_count_5m": case.count_5m,
                    "amount_cents_5m": case.amount_5m,
                    "amount_cents_60m": case.amount_60m,
                },
                "evidence": {
                    "prior_attempt_count": case.prior_count,
                    "counterparty": case.counterparty,
                    "device": case.device,
                    "profile": case.profile,
                    "profile_median_cents": case.median,
                    "distinct_counterparties_60m": case.diversity,
                    "geolocation": case.geo,
                    "payer_type": case.payer_type,
                    "payee_type": case.payee_type,
                },
                "v1_status": v1_result["status"],
                "v2_status": v2_result["status"],
                "v2_r2_status": v2_r2_result["status"],
                "v3_status": v3_result["status"],
                "ablation_status": ablation_result["status"],
                "v2_score": policy["risk_score"],
                "v2_breakdown": policy["families"],
                "v2_reason": v2_result["metadata"]["reason_code"],
                "v2_r2_score": policy_r2["risk_score"],
                "v2_r2_breakdown": policy_r2["families"],
                "v2_r2_reason": v2_r2_result["metadata"]["reason_code"],
                "v3_score": policy_v3["risk_score"],
                "v3_breakdown": policy_v3["families"],
                "v3_reason": v3_result["metadata"]["reason_code"],
                "v1_matches_intention": v1_result["status"] == LABEL_TO_STATUS[case.label],
                "v2_matches_intention": v2_result["status"] == LABEL_TO_STATUS[case.label],
                "v2_r2_matches_intention": v2_r2_result["status"] == LABEL_TO_STATUS[case.label],
                "v3_matches_intention": v3_result["status"] == LABEL_TO_STATUS[case.label],
            }
        )
    return {
        "seed": SEED,
        "parameters_frozen_before_c2": True,
        "r2_parameters_frozen_before_c3": True,
        "c2_role": "historical_diagnostic_not_holdout",
        "c3_role": "reserved_synthetic_evaluation",
        "v3_evaluation_role": "retrospective_projection_not_independent_holdout",
        "results": results,
        "metrics": metrics(results),
    }


def metrics(results: list[dict]) -> dict:
    output = {}
    for experiment in ("B2", "C2", "C3"):
        subset = [row for row in results if row["experiment"] == experiment]
        output[experiment] = {}
        for policy in ("v1", "v2", "v2_r2", "v3", "ablation"):
            status_key = f"{policy}_status"
            legitimate = [row for row in subset if row["synthetic_label"] == "legitimo"]
            critical = [row for row in subset if row["synthetic_label"] == "critico"]
            output[experiment][policy] = {
                "matches_intention": sum(
                    row[status_key] == row["expected_status"] for row in subset
                ),
                "total": len(subset),
                "legitimate_friction": sum(
                    row[status_key] != "approved" for row in legitimate
                ),
                "legitimate_rejections": sum(
                    row[status_key] == "rejected" for row in legitimate
                ),
                "critical_approvals": sum(
                    row[status_key] == "approved" for row in critical
                ),
                "critical_two_factor": sum(
                    row[status_key] == "two_factor_required" for row in critical
                ),
                "critical_rejections": sum(
                    row[status_key] == "rejected" for row in critical
                ),
                "status_counts_by_label": {
                    label: {
                        status: sum(
                            row["synthetic_label"] == label and row[status_key] == status
                            for row in subset
                        )
                        for status in ("approved", "two_factor_required", "rejected")
                    }
                    for label in ("legitimo", "duvida", "critico")
                },
            }
        output[experiment]["critical_rejection_downgrades_v2_vs_v1"] = sum(
            row["v1_status"] == "rejected" and row["v2_status"] != "rejected"
            for row in critical
        )
        output[experiment]["critical_rejection_downgrades_r2_vs_v1"] = sum(
            row["v1_status"] == "rejected" and row["v2_r2_status"] != "rejected"
            for row in critical
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        help="Optional path for the complete JSON result; stdout is always printed.",
    )
    args = parser.parse_args()
    result = run()
    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
