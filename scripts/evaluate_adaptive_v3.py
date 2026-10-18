"""Caracteriza V1, V2 R2 e V3 em cenários sintéticos de cobertura ampliada.

Este conjunto foi criado junto da V3: serve como teste de comportamento e não
como holdout independente nem como evidência de eficácia em produção.
"""

import json
import os
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://offline:offline@localhost/offline_not_connected",
)

from controllers.pix_adaptive_risk_policy import PixAdaptiveRiskPolicy  # noqa: E402
from controllers.pix_contextual_risk_policy import (  # noqa: E402
    REVISED_CONTEXTUAL_RISK_CONFIG,
    PixContextualRiskPolicy,
)
from controllers.pix_risk_policy import PixRiskPolicy  # noqa: E402


def _merge(target: dict, override: dict) -> dict:
    result = deepcopy(target)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _base() -> dict:
    return {
        "schema_version": "pix-history-v1",
        "prior_attempt_count": 8,
        "counterparty": {"observation": "observed"},
        "windows": {
            "1m": {"attempt_count": 1, "amount_cents": 10_000},
            "5m": {"attempt_count": 1, "amount_cents": 10_000},
            "15m": {"attempt_count": 1, "amount_cents": 10_000},
            "60m": {"attempt_count": 1, "amount_cents": 10_000},
            "1440m": {"attempt_count": 1, "amount_cents": 10_000},
        },
        "contextual": {
            "schema_version": "pix-context-v3-r4-v4",
            "profile": {
                "observation": "observed",
                "prior_attempt_count": 8,
                "history_span_seconds": 604_800,
                "median_amount_cents": 50_000,
                "p90_amount_cents": 100_000,
            },
            "relationship": {
                "prior_counterparty_attempt_count": 4,
                "pair_attempt_count_15m": 1,
                "distinct_counterparty_count_60m": 1,
            },
            "access": {
                "device_observation": "observed",
                "ip_observation": "observed",
                "channel_observation": "observed",
                "distinct_device_count_60m": 1,
                "distinct_ip_count_60m": 1,
                "device_distinct_payer_count_60m": 1,
                "ip_distinct_payer_count_60m": 1,
            },
            "temporal": {
                "transaction_clock_skew_seconds": 0,
                "nighttime_observation": "habitual",
                "geolocation": {"observation": "missing"},
            },
            "beneficiary": {
                "global_observation": "observed",
                "distinct_payer_count_60m": 1,
                "attempt_count_60m": 1,
                "destination_account_age_days": 365,
            },
            "party_types": {"payer": "pf", "payee": "pf"},
            "account_context": {
                "source_account_age_days": 365,
                "is_self_transfer": False,
            },
        },
    }


def _v2_snapshot(v3: dict) -> dict:
    profile = v3["contextual"]["profile"]
    access = v3["contextual"]["access"]
    relationship = v3["contextual"]["relationship"]
    temporal = v3["contextual"]["temporal"]
    result = deepcopy(v3)
    result["contextual"] = {
        "schema_version": "pix-context-v2-candidate-v1",
        "device": {
            "observation": access["device_observation"],
            "prior_attempt_count": 0,
        },
        "profile": {
            "observation": profile["observation"],
            "prior_attempt_count": profile["prior_attempt_count"],
            "history_span_seconds": profile["history_span_seconds"],
            "median_amount_cents": profile["median_amount_cents"],
        },
        "diversity": {
            "60m": {
                "distinct_counterparty_count": relationship[
                    "distinct_counterparty_count_60m"
                ]
            }
        },
        "geolocation": temporal["geolocation"],
        "source_availability": {"channel": "observed", "ip": "observed"},
        "party_types": v3["contextual"]["party_types"],
    }
    return result


def scenarios() -> list[dict]:
    cold = {
        "prior_attempt_count": 0,
        "counterparty": {"observation": "insufficient_history"},
        "contextual": {
            "profile": {
                "observation": "insufficient_history",
                "prior_attempt_count": 0,
                "history_span_seconds": 0,
                "median_amount_cents": None,
                "p90_amount_cents": None,
            },
            "access": {
                "device_observation": "insufficient_history",
                "ip_observation": "insufficient_history",
                "channel_observation": "insufficient_history",
            },
        },
    }
    return [
        {
            "id": "A01",
            "description": "rotina habitual",
            "expected": "approved",
            "amount": 10_000,
            "override": {},
        },
        {
            "id": "A02",
            "description": "R$ 5 mil em cold start",
            "expected": "two_factor_required",
            "amount": 500_000,
            "override": _merge(
                cold,
                {
                    "contextual": {
                        "account_context": {
                            "source_account_age_days": 5,
                            "is_self_transfer": False,
                        }
                    }
                },
            ),
        },
        {
            "id": "A03",
            "description": "rajada crítica objetiva",
            "expected": "rejected",
            "amount": 375_000,
            "override": {
                "windows": {
                    "1m": {"attempt_count": 4, "amount_cents": 1_500_000},
                    "5m": {"attempt_count": 8, "amount_cents": 3_000_000},
                    "15m": {"attempt_count": 8, "amount_cents": 3_000_000},
                    "60m": {"attempt_count": 8, "amount_cents": 3_000_000},
                    "1440m": {"attempt_count": 8, "amount_cents": 3_000_000},
                }
            },
        },
        {
            "id": "A04",
            "description": "acúmulo em 24h fora da janela de 60m",
            "expected": "two_factor_required",
            "amount": 100_000,
            "override": {
                "windows": {
                    "1440m": {
                        "attempt_count": 20,
                        "amount_cents": 5_000_000,
                    }
                }
            },
        },
        {
            "id": "A05",
            "description": "tomada de conta contextual",
            "expected": "rejected",
            "amount": 100_000,
            "override": {
                "counterparty": {"observation": "not_observed"},
                "contextual": {
                    "relationship": {
                        "prior_counterparty_attempt_count": 0,
                        "distinct_counterparty_count_60m": 5,
                    },
                    "access": {
                        "device_observation": "newly_observed",
                        "ip_observation": "newly_observed",
                        "channel_observation": "changed",
                        "distinct_device_count_60m": 3,
                        "distinct_ip_count_60m": 3,
                    },
                    "temporal": {
                        "transaction_clock_skew_seconds": 9_000,
                        "nighttime_observation": "unusual",
                        "geolocation": {
                            "observation": "observed",
                            "distance_km": 900.0,
                            "elapsed_seconds": 1_800,
                            "speed_kmh": 1_800.0,
                        },
                    },
                },
            },
        },
        {
            "id": "A06",
            "description": "fan-in, conta jovem e IP compartilhado",
            "expected": "two_factor_required",
            "amount": 400_000,
            "override": _merge(
                cold,
                {
                    "contextual": {
                        "temporal": {"transaction_clock_skew_seconds": 9_000},
                        "access": {"ip_distinct_payer_count_60m": 8},
                        "beneficiary": {
                            "global_observation": "observed",
                            "distinct_payer_count_60m": 8,
                            "attempt_count_60m": 8,
                            "destination_account_age_days": 2,
                        },
                    }
                },
            ),
        },
        {
            "id": "A07",
            "description": "desvio de perfil, novo destino e novo acesso",
            "expected": "two_factor_required",
            "amount": 300_000,
            "override": {
                "counterparty": {"observation": "not_observed"},
                "contextual": {
                    "profile": {
                        "median_amount_cents": 10_000,
                        "p90_amount_cents": 20_000,
                    },
                    "relationship": {
                        "prior_counterparty_attempt_count": 0,
                        "distinct_counterparty_count_60m": 4,
                    },
                    "access": {
                        "device_observation": "newly_observed",
                        "ip_observation": "newly_observed",
                    },
                },
            },
        },
        {
            "id": "A08",
            "description": "lote PJ legítimo na fronteira crítica",
            "expected": "approved",
            "amount": 400_000,
            "override": {
                "windows": {
                    "1m": {"attempt_count": 4, "amount_cents": 1_600_000},
                    "5m": {"attempt_count": 8, "amount_cents": 3_200_000},
                    "15m": {"attempt_count": 8, "amount_cents": 3_200_000},
                    "60m": {"attempt_count": 8, "amount_cents": 3_200_000},
                    "1440m": {"attempt_count": 8, "amount_cents": 3_200_000},
                },
                "contextual": {
                    "profile": {
                        "median_amount_cents": 400_000,
                        "p90_amount_cents": 450_000,
                    },
                    "party_types": {"payer": "pj", "payee": "pj"},
                },
            },
        },
        {
            "id": "A09",
            "description": "convergência crítica de quatro famílias",
            "expected": "rejected",
            "amount": 300_000,
            "override": {
                "counterparty": {"observation": "not_observed"},
                "contextual": {
                    "profile": {
                        "median_amount_cents": 10_000,
                        "p90_amount_cents": 20_000,
                    },
                    "relationship": {
                        "prior_counterparty_attempt_count": 0,
                        "distinct_counterparty_count_60m": 8,
                    },
                    "access": {
                        "device_observation": "newly_observed",
                        "ip_observation": "newly_observed",
                        "channel_observation": "changed",
                        "distinct_device_count_60m": 3,
                        "distinct_ip_count_60m": 3,
                    },
                    "beneficiary": {
                        "global_observation": "observed",
                        "distinct_payer_count_60m": 8,
                        "attempt_count_60m": 10,
                        "destination_account_age_days": 365,
                    },
                },
            },
        },
        {
            "id": "A10",
            "description": "velocidade geográfica impossível sem anomalia de acesso",
            "expected": "two_factor_required",
            "amount": 10_000,
            "override": {
                "contextual": {
                    "temporal": {
                        "geolocation": {
                            "observation": "observed",
                            "distance_km": 400.0,
                            "elapsed_seconds": 600,
                            "speed_kmh": 2_400.0,
                        }
                    }
                }
            },
        },
        {
            "id": "A11",
            "description": "desvio crítico isolado do perfil",
            "expected": "two_factor_required",
            "amount": 400_000,
            "override": {},
        },
        {
            "id": "A12",
            "description": "desvio comportamental para novo destinatário",
            "expected": "two_factor_required",
            "amount": 150_000,
            "override": {"counterparty": {"observation": "not_observed"}},
        },
        {
            "id": "A13",
            "description": "fan-out crítico isolado",
            "expected": "two_factor_required",
            "amount": 10_000,
            "override": {
                "contextual": {
                    "relationship": {"distinct_counterparty_count_60m": 8}
                }
            },
        },
        {
            "id": "A14",
            "description": "fan-in crítico isolado",
            "expected": "two_factor_required",
            "amount": 10_000,
            "override": {
                "contextual": {
                    "beneficiary": {
                        "global_observation": "observed",
                        "distinct_payer_count_60m": 5,
                        "attempt_count_60m": 8,
                        "destination_account_age_days": 365,
                    }
                }
            },
        },
        {
            "id": "A15",
            "description": "device compartilhado por três pagadores PF",
            "expected": "two_factor_required",
            "amount": 10_000,
            "override": {
                "contextual": {
                    "access": {"device_distinct_payer_count_60m": 3}
                }
            },
        },
        {
            "id": "A16",
            "description": "conta-laranja jovem em primeira observação",
            "expected": "two_factor_required",
            "amount": 300_000,
            "override": {
                "prior_attempt_count": 3,
                "counterparty": {"observation": "not_observed"},
                "contextual": {
                    "profile": {
                        "observation": "insufficient_history",
                        "prior_attempt_count": 3,
                        "history_span_seconds": 0,
                        "median_amount_cents": None,
                        "p90_amount_cents": None,
                    },
                    "beneficiary": {
                        "global_observation": "not_observed",
                        "distinct_payer_count_60m": 1,
                        "attempt_count_60m": 1,
                        "destination_account_age_days": 2,
                    },
                },
            },
        },
        {
            "id": "A17",
            "description": "novo destinatário em valor habitual",
            "expected": "approved",
            "amount": 50_000,
            "override": {"counterparty": {"observation": "not_observed"}},
        },
        {
            "id": "A18",
            "description": "conta jovem com valor abaixo do material",
            "expected": "approved",
            "amount": 100_000,
            "override": {
                "counterparty": {"observation": "not_observed"},
                "contextual": {
                    "beneficiary": {
                        "global_observation": "not_observed",
                        "distinct_payer_count_60m": 1,
                        "attempt_count_60m": 1,
                        "destination_account_age_days": 2,
                    }
                },
            },
        },
        {
            "id": "A19",
            "description": "device corporativo compartilhado sem outro risco",
            "expected": "approved",
            "amount": 10_000,
            "override": {
                "contextual": {
                    "access": {"device_distinct_payer_count_60m": 3},
                    "party_types": {"payer": "pj", "payee": "pf"},
                }
            },
        },
    ]


def run() -> dict:
    policies = {
        "v1": PixRiskPolicy(),
        "v2_r2": PixContextualRiskPolicy(REVISED_CONTEXTUAL_RISK_CONFIG),
        "v3": PixAdaptiveRiskPolicy(),
    }
    rows = []
    for case in scenarios():
        v3_snapshot = _merge(_base(), case["override"])
        snapshots = {
            "v1": v3_snapshot,
            "v2_r2": _v2_snapshot(v3_snapshot),
            "v3": v3_snapshot,
        }
        row = {key: case[key] for key in ("id", "description", "expected")}
        for name, policy in policies.items():
            decision = policy.evaluate(
                {"amount_cents": case["amount"]}, snapshots[name]
            )
            row[f"{name}_status"] = decision["status"]
            if name != "v1":
                row[f"{name}_score"] = decision["evidence_snapshot"]["policy"][
                    "risk_score"
                ]
                row[f"{name}_families"] = decision["evidence_snapshot"]["policy"][
                    "families"
                ]
            if name == "v3":
                row["v3_challenge"] = decision["metadata"].get("challenge")
                row["v3_reason"] = decision["metadata"]["reason_code"]
                row["v3_risk_factors"] = decision["metadata"].get(
                    "risk_factors", []
                )
        rows.append(row)
    return {
        "role": "synthetic_behavior_characterization_not_independent_holdout",
        "current_policy_version": PixAdaptiveRiskPolicy().config.policy_version,
        "results": rows,
        "matches_intention": {
            name: sum(row[f"{name}_status"] == row["expected"] for row in rows)
            for name in policies
        },
        "total": len(rows),
    }


def main() -> None:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
