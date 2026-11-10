"""Cycling-only heart-rate zone derivation in ``compute_all_zones``.

A cycling-only athlete has LTHR recorded on the Ride sport row
(``cycling_lthr_bpm``) with no running LTHR (``lthr_bpm``). Regression for
the bug where the cycling heart-rate ``ZoneSet`` was only ever built inside
the ``lthr_bpm`` branch, so a cycling-only athlete got no HR zones at all.
"""

from __future__ import annotations

from endury.domain.analysis.zones import compute_all_zones


def test_cycling_only_lthr_produces_one_cycling_heart_rate_zone_set():
    zones = compute_all_zones(cycling_lthr_bpm=150)

    hr_sets = [zs for zs in zones.zone_sets if zs.metric == "heart_rate"]
    assert len(hr_sets) == 1
    assert hr_sets[0].sport == "cycling"
    assert len(hr_sets[0].bands) == 5


def test_running_lthr_still_derives_cycling_zones_when_absent():
    zones = compute_all_zones(lthr_bpm=160)

    hr_sets = {zs.sport: zs for zs in zones.zone_sets if zs.metric == "heart_rate"}
    assert set(hr_sets) == {"running", "cycling"}
