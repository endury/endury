import pytest

from bench.brief import build_brief
from bench.rules import load_rulebook
from bench.scenarios import MATRIX, by_label


def test_blind_brief_states_the_athlete_and_the_shape_but_no_band():
    sc = by_label("ironman_32wk_competitive")
    brief = build_brief(sc)
    assert "32-week" in brief
    assert "2026-08-16" in brief
    assert "bike: 5.1 h/week; longest recent session 90 min" in brief
    assert '"steps"' in brief
    assert "Zones:" in brief
    assert "target" not in brief
    assert "checked against" not in brief
    assert "Preferences: none" in brief


def test_bands_arm_appends_the_applicable_targets_and_caps_are_stated():
    rb = load_rulebook()
    sc = by_label("ironman_32wk_competitive_cap12")
    brief = build_brief(sc, rb, "bands")
    assert "at or under 12 hours" in brief
    assert "checked against these targets" in brief
    assert "taper_volume_drop" in brief
    with pytest.raises(ValueError, match="needs the rulebook"):
        build_brief(sc, None, "bands")
    with pytest.raises(ValueError, match="unknown arm"):
        build_brief(sc, rb, "loop")


def test_matrix_labels_are_unique_and_headline_cells_exist():
    labels = [s.label for s in MATRIX]
    assert len(labels) == len(set(labels))
    assert by_label("marathon_18wk_moderate").athlete.hours == {"run": 3.0}
    with pytest.raises(KeyError):
        by_label("nope")
