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
    PixContextualRiskPolicy,
)
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
    return b2 + c2


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


def run() -> dict:
    random.seed(SEED)
    v1 = PixRiskPolicy()
    v2 = PixContextualRiskPolicy()
    ablation = PixContextualRiskPolicy(ContextualRiskConfig.ablation())
    results = []
    for case in scenarios():
        snapshot = evidence(case)
        transaction = {"amount_cents": case.amount}
        v1_result = v1.evaluate(transaction, snapshot)
        v2_result = v2.evaluate(transaction, snapshot)
        ablation_result = ablation.evaluate(transaction, snapshot)
        policy = v2_result["evidence_snapshot"]["policy"]
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
                "ablation_status": ablation_result["status"],
                "v2_score": policy["risk_score"],
                "v2_breakdown": policy["families"],
                "v2_reason": v2_result["metadata"]["reason_code"],
                "v1_matches_intention": v1_result["status"] == LABEL_TO_STATUS[case.label],
                "v2_matches_intention": v2_result["status"] == LABEL_TO_STATUS[case.label],
            }
        )
    return {"seed": SEED, "parameters_frozen_before_c2": True, "results": results, "metrics": metrics(results)}


def metrics(results: list[dict]) -> dict:
    output = {}
    for experiment in ("B2", "C2"):
        subset = [row for row in results if row["experiment"] == experiment]
        output[experiment] = {}
        for policy in ("v1", "v2", "ablation"):
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
            }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(ROOT / "docs" / "PIX_SENTINEL_RESULTADOS_V2_A_B2_C2.json"),
    )
    args = parser.parse_args()
    result = run()
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
