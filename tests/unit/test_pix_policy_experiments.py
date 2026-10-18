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


def test_adaptive_v3_characterization_expands_coverage_and_keeps_known_false_positive():
    result = run_v3()
    rows = {row["id"]: row for row in result["results"]}

    assert result["role"] == "synthetic_behavior_characterization_not_independent_holdout"
    assert result["matches_intention"] == {"v1": 3, "v2_r2": 4, "v3": 9}
    assert rows["A05"]["v3_score"] == 57
    assert rows["A05"]["v3_status"] == "rejected"
    assert rows["A06"]["v3_score"] == 36
    assert rows["A08"]["v3_status"] == "rejected"
    assert rows["A08"]["expected"] == "approved"
    assert rows["A09"]["v3_status"] == "rejected"
    assert rows["A10"]["v3_status"] == "two_factor_required"
