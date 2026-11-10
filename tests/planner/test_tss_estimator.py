"""Tests for endury/planner/macro/tss_estimator.py."""

from __future__ import annotations

import pytest

from endury.planner.foundations.types import TIDFractions
from endury.planner.macro.tss_estimator import ZONE_TSS_PER_HOUR, tss_per_hour


class TestZoneTssPerHourConstants:
    """Verify zone constants match IF² x 100 formula."""

    def test_z_low(self) -> None:
        assert ZONE_TSS_PER_HOUR["z_low"] == pytest.approx(0.65**2 * 100)

    def test_z_mid(self) -> None:
        assert ZONE_TSS_PER_HOUR["z_mid"] == pytest.approx(0.875**2 * 100)

    def test_z_high(self) -> None:
        assert ZONE_TSS_PER_HOUR["z_high"] == pytest.approx(1.05**2 * 100)

    def test_z_low_value(self) -> None:
        assert ZONE_TSS_PER_HOUR["z_low"] == pytest.approx(42.25)

    def test_z_mid_value(self) -> None:
        assert ZONE_TSS_PER_HOUR["z_mid"] == pytest.approx(76.5625)

    def test_z_high_value(self) -> None:
        assert ZONE_TSS_PER_HOUR["z_high"] == pytest.approx(110.25)

    def test_zones_ordered_ascending(self) -> None:
        assert (
            ZONE_TSS_PER_HOUR["z_low"]
            < ZONE_TSS_PER_HOUR["z_mid"]
            < ZONE_TSS_PER_HOUR["z_high"]
        )


class TestTssPerHour:
    """Verify tss_per_hour returns correct weighted averages."""

    def test_pure_z_low(self) -> None:
        fractions = TIDFractions(z_low=1.0, z_mid=0.0, z_high=0.0)
        assert tss_per_hour(fractions) == pytest.approx(42.25)

    def test_pure_z_mid(self) -> None:
        fractions = TIDFractions(z_low=0.0, z_mid=1.0, z_high=0.0)
        assert tss_per_hour(fractions) == pytest.approx(76.5625)

    def test_pure_z_high(self) -> None:
        fractions = TIDFractions(z_low=0.0, z_mid=0.0, z_high=1.0)
        assert tss_per_hour(fractions) == pytest.approx(110.25)

    def test_equal_thirds(self) -> None:
        fractions = TIDFractions(z_low=1 / 3, z_mid=1 / 3, z_high=1 / 3)
        expected = (42.25 + 76.5625 + 110.25) / 3
        assert tss_per_hour(fractions) == pytest.approx(expected)

    def test_typical_base_marathon(self) -> None:
        # BASE LOAD marathon: TID 0.75/0.20/0.05
        fractions = TIDFractions(z_low=0.75, z_mid=0.20, z_high=0.05)
        expected = 0.75 * 42.25 + 0.20 * 76.5625 + 0.05 * 110.25
        assert tss_per_hour(fractions) == pytest.approx(expected)

    def test_typical_build_5k(self) -> None:
        # BUILD LOAD 5K: TID 0.40/0.35/0.25
        fractions = TIDFractions(z_low=0.40, z_mid=0.35, z_high=0.25)
        expected = 0.40 * 42.25 + 0.35 * 76.5625 + 0.25 * 110.25
        assert tss_per_hour(fractions) == pytest.approx(expected)

    def test_linearity(self) -> None:
        """tss_per_hour is a linear function of fractions."""
        f1 = TIDFractions(z_low=0.8, z_mid=0.15, z_high=0.05)
        f2 = TIDFractions(z_low=0.5, z_mid=0.3, z_high=0.2)
        # midpoint
        f_mid = TIDFractions(
            z_low=(f1.z_low + f2.z_low) / 2,
            z_mid=(f1.z_mid + f2.z_mid) / 2,
            z_high=(f1.z_high + f2.z_high) / 2,
        )
        assert tss_per_hour(f_mid) == pytest.approx(
            (tss_per_hour(f1) + tss_per_hour(f2)) / 2
        )

    def test_result_within_zone_bounds(self) -> None:
        """For any valid fractions summing to 1, result is between z_low and z_high."""
        fractions = TIDFractions(z_low=0.6, z_mid=0.3, z_high=0.1)
        result = tss_per_hour(fractions)
        assert ZONE_TSS_PER_HOUR["z_low"] <= result <= ZONE_TSS_PER_HOUR["z_high"]

    def test_recovery_week_low_intensity(self) -> None:
        # RECOVERY: 0.90/0.08/0.02 → closer to z_low floor
        fractions = TIDFractions(z_low=0.90, z_mid=0.08, z_high=0.02)
        result = tss_per_hour(fractions)
        assert result < 50.0  # dominated by z_low

    def test_peak_high_intensity_week(self) -> None:
        # PEAK LOAD 5K: 0.35/0.30/0.35
        fractions = TIDFractions(z_low=0.35, z_mid=0.30, z_high=0.35)
        result = tss_per_hour(fractions)
        assert result > 70.0  # high-intensity week raises TSS/h significantly
