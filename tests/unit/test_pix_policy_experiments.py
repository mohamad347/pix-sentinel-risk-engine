from scripts.evaluate_contextual_v2 import run
from scripts.evaluate_adaptive_v3 import run as run_v3


def test_fixed_b2_c2_c3_experiment_compares_all_policies_without_recalibrating_c3():
    result = run()

    assert result["seed"] == 20261018
    assert result["parameters_frozen_before_c2"] is True
    assert result["r2_parameters_frozen_before_c3"] is True
    assert result["c2_role"] == "historical_diagnostic_not_holdout"
    assert result["c3_role"] == "reserved_synthetic_evaluation"
    assert len(result["results"]) == 30
    assert len({row["id"] for row in result["results"]}) == 30

    assert result["metrics"]["B2"]["v2"]["matches_intention"] == 6
    assert result["metrics"]["C2"]["v2"]["matches_intention"] == 5
    assert result["metrics"]["C2"]["v2"]["matches_intention"] > result["metrics"]["C2"]["ablation"]["matches_intention"]
    assert result["metrics"]["C2"]["v2"]["matches_intention"] > result["metrics"]["C2"]["v1"]["matches_intention"]
    assert result["metrics"]["C3"]["v2_r2"]["matches_intention"] == 7
    assert result["metrics"]["C3"]["v2_r2"]["critical_approvals"] == 0
    assert result["metrics"]["C3"]["critical_rejection_downgrades_v2_vs_v1"] == 1
    assert result["metrics"]["C3"]["critical_rejection_downgrades_r2_vs_v1"] == 0


def test_adaptive_v3_characterization_resolves_pj_false_rejection_without_friction():
    result = run_v3()
    rows = {row["id"]: row for row in result["results"]}

    assert result["role"] == "synthetic_behavior_characterization_not_independent_holdout"
    assert result["current_policy_version"] == "mvp-risk-adaptive-v3-r9"
    assert result["total"] == 22
    assert result["matches_intention"]["v3"] == 22
    assert rows["A02"]["v3_reason"] == "ADAPTIVE_COLD_START_VERIFICATION"
    assert "FIRST_OBSERVED_YOUNG_SOURCE_ACCOUNT" in rows["A02"][
        "v3_risk_factors"
    ]
    assert rows["A05"]["v3_score"] == 57
    assert rows["A05"]["v3_status"] == "rejected"
    assert rows["A06"]["v3_score"] == 36
    assert rows["A08"]["v3_status"] == "approved"
    assert rows["A08"]["expected"] == "approved"
    assert rows["A09"]["v3_status"] == "rejected"
    assert rows["A10"]["v3_status"] == "two_factor_required"
    for scenario_id in ("A11", "A12", "A13", "A14", "A15", "A16"):
        assert rows[scenario_id]["v3_status"] == "two_factor_required"
        assert rows[scenario_id]["v3_base_score"] < 25
        assert rows[scenario_id]["v3_score"] == 25
    for scenario_id in ("A17", "A18", "A19", "A20", "A21"):
        assert rows[scenario_id]["v3_status"] == "approved"
    assert rows["A20"]["v3_score"] == 10
    assert rows["A21"]["v3_score"] == 15
    assert rows["A22"]["v3_score"] == 25
    assert rows["A22"]["v3_status"] == "two_factor_required"
    assert {"VERY_HIGH_ABSOLUTE_AMOUNT", "UNUSUAL_NIGHTTIME"} <= set(
        rows["A22"]["v3_risk_factors"]
    )
    assert rows["A11"]["v3_reason"] == "ADAPTIVE_CRITICAL_PROFILE_VERIFICATION"
    assert rows["A12"]["v3_reason"] == (
        "ADAPTIVE_BEHAVIORAL_RELATIONSHIP_VERIFICATION"
    )
    assert rows["A16"]["v3_reason"] == "ADAPTIVE_EARLY_MULE_VERIFICATION"
