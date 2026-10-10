"""Duct sizing arithmetic: self-consistency, monotonic behaviour, caps, the balance check and setting validation. No database."""
import math

import pytest
from mep import sizing

S = sizing.clean_settings({})


def test_a_round_duct_hits_its_friction_target_and_matches_the_usual_chart_range():
    d = sizing.round_diameter(1.0, 1.0, 0.09e-3) * 1000
    assert 400 < d < 500                                                                         # hand check: v = 6.5 m/s, f = 0.0165, dp/L = f rho v^2 / 2D = 0.96 Pa/m
    assert sizing.friction_pa_per_m(1.0, d / 1000, 0.09e-3) == pytest.approx(1.0, rel=1e-3)
    assert sizing.round_diameter(2.0, 1.0, 0.09e-3) > sizing.round_diameter(1.0, 1.0, 0.09e-3)    # more air, bigger duct
    assert sizing.round_diameter(1.0, 0.5, 0.09e-3) > sizing.round_diameter(1.0, 1.0, 0.09e-3)    # lower friction, bigger duct


def test_a_rectangle_is_at_least_the_equivalent_round_and_rounds_up_to_the_increment():
    r = sizing.size_duct(1000, "rect", S)
    assert r.width_mm % 50 == 0 and r.depth_mm % 50 == 0 and r.width_mm >= r.depth_mm
    assert sizing.equivalent_diameter(r.width_mm, r.depth_mm) >= r.equivalent_diameter_mm - 1e-6
    assert r.friction_actual_pa_m <= r.friction_target_pa_m + 1e-9                             # rounding up only lowers friction
    assert r.velocity_ms == pytest.approx(1.0 / (r.width_mm * r.depth_mm / 1e6), rel=1e-3)


def test_the_depth_limit_and_aspect_note():
    free = sizing.size_duct(1000, "rect", S)
    capped = sizing.size_duct(1000, "rect", S, depth_cap_mm=250)
    assert capped.depth_mm <= 250 < free.depth_mm and capped.width_mm > free.width_mm
    assert any("depth" in n for n in capped.notes)
    assert any("aspect" in n for n in sizing.size_duct(1000, "rect", sizing.clean_settings({"max_aspect": 1.5}), depth_cap_mm=250).notes)


def test_small_flows_respect_the_minimum_size_and_round_ducts_work():
    assert sizing.size_duct(20, "rect", S).width_mm >= 150 and sizing.size_duct(20, "round", S).diameter_mm >= 150
    rd = sizing.size_duct(500, "round", S)
    assert rd.diameter_mm % 50 == 0 and rd.width_mm is None and math.isfinite(rd.velocity_ms)


def test_velocity_notes_read_only_firm_limits():
    assert sizing.velocity_note(30, "main", S) == "NO LIMIT SET"
    lim = sizing.clean_settings({"max_velocity_main_ms": 8, "max_velocity_branch_ms": 5})
    assert sizing.velocity_note(8, "main", lim) == "WITHIN LIMIT" and sizing.velocity_note(6, "branch", lim) == "ABOVE LIMIT"


def test_balance_per_system():
    runs = [{"kind": "duct", "system_tag": "S1", "airflow_ls": 500}, {"kind": "duct", "system_tag": "S1", "airflow_ls": 250},
            {"kind": "terminal", "system_tag": "S1", "airflow_ls": 250, "quantity": 2},
            {"kind": "duct", "system_tag": "S2", "airflow_ls": 500}, {"kind": "terminal", "system_tag": "S2", "airflow_ls": 200, "quantity": 2},
            {"kind": "duct", "system_tag": "S3", "airflow_ls": 100}]
    got = {b["system_tag"]: b for b in sizing.balance(runs, 1.0)}
    assert got["S1"]["status"] == "BALANCED" and got["S2"]["status"] == "UNBALANCED" and got["S2"]["difference_pct"] == 20.0 and got["S3"]["status"] == "NO DATA"


@pytest.mark.parametrize("bad", [{"nope": 1}, {"friction_pa_m": 0}, {"friction_pa_m": float("nan")}, {"size_increment_mm": 1000}, {"max_velocity_main_ms": True}, {"roughness_mm": "x"}])
def test_settings_are_validated(bad):
    with pytest.raises(ValueError):
        sizing.clean_settings(bad)


def test_bad_airflow_is_refused():
    for q in (0, -5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            sizing.size_duct(q, "rect", S)
