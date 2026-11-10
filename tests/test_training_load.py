"""Tests for shared training-load helpers."""

from endury.training.load import (
    RUNNING_THRESHOLD_TSS_PER_HOUR,
    duration_minutes_from_tss,
    pace_sec_per_100m_from_css,
    positive_float_or_none,
    tss_per_hour_at_if,
)


def test_positive_float_or_none_rejects_non_positive_values() -> None:
    assert positive_float_or_none(0) is None
    assert positive_float_or_none(-3) is None


def test_positive_float_or_none_rejects_non_numeric() -> None:
    assert positive_float_or_none(None) is None
    assert positive_float_or_none("abc") is None


def test_positive_float_or_none_returns_positive() -> None:
    assert positive_float_or_none(3.5) == 3.5
    assert positive_float_or_none(1) == 1.0


def test_pace_sec_per_100m_from_css() -> None:
    assert pace_sec_per_100m_from_css(100.0, 1.0) == 100.0
    assert pace_sec_per_100m_from_css(100.0, 0.5) == 200.0


def test_duration_minutes_from_tss_returns_zero_when_rate_is_zero() -> None:
    assert duration_minutes_from_tss(25.0, 0.0, exponent=2.0) == 0.0


def test_running_threshold_tss_per_hour_uses_rtss_scale() -> None:
    assert (
        tss_per_hour_at_if(
            1.0,
            exponent=2.0,
            threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        )
        == RUNNING_THRESHOLD_TSS_PER_HOUR
    )


def test_thresholds_are_fresh_requires_context_and_recency() -> None:
    from datetime import date
    from types import SimpleNamespace

    from endury.training.load import THRESHOLD_MAX_AGE_DAYS, thresholds_are_fresh

    today = date(2026, 7, 13)
    assert thresholds_are_fresh(None, today) is False
    assert (
        thresholds_are_fresh(SimpleNamespace(threshold_updated_at=None), today) is True
    )
    fresh = SimpleNamespace(threshold_updated_at=date(2026, 7, 1))
    assert thresholds_are_fresh(fresh, today) is True
    from datetime import timedelta

    stale = SimpleNamespace(
        threshold_updated_at=today - timedelta(days=THRESHOLD_MAX_AGE_DAYS + 1)
    )
    assert thresholds_are_fresh(stale, today) is False
