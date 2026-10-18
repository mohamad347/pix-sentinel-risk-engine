from scripts.evaluate_contextual_v2 import run


def test_fixed_b2_c2_experiment_compares_v1_v2_and_ablation_without_recalibrating_c2():
    result = run()

    assert result["seed"] == 20261018
    assert result["parameters_frozen_before_c2"] is True
    assert len(result["results"]) == 18
    assert len({row["id"] for row in result["results"]}) == 18

    assert result["metrics"]["B2"]["v2"] == {
        "matches_intention": 6,
        "total": 9,
        "legitimate_friction": 0,
        "legitimate_rejections": 0,
        "critical_approvals": 0,
    }
    assert result["metrics"]["C2"]["v2"] == {
        "matches_intention": 5,
        "total": 9,
        "legitimate_friction": 1,
        "legitimate_rejections": 0,
        "critical_approvals": 0,
    }
    assert result["metrics"]["C2"]["v2"]["matches_intention"] > result["metrics"]["C2"]["ablation"]["matches_intention"]
    assert result["metrics"]["C2"]["v2"]["matches_intention"] > result["metrics"]["C2"]["v1"]["matches_intention"]
