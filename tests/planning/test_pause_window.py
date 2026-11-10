"""Tests for pause_training's from_date guard (validate_from_date)."""

from __future__ import annotations

from datetime import date

from endury.planning.pause_training import validate_from_date

_TODAY = date(2026, 5, 20)  # a Wednesday
_THROUGH = date(2026, 5, 27)  # the following Wednesday


class TestOmittedOrToday:
    def test_omitted_proceeds(self) -> None:
        assert validate_from_date(None, through=_THROUGH, today=_TODAY) is None

    def test_equal_to_today_proceeds(self) -> None:
        result = validate_from_date("2026-05-20", through=_THROUGH, today=_TODAY)
        assert result is None


class TestInvalidOrOutOfRange:
    def test_unparsable_date_is_an_error(self) -> None:
        result = validate_from_date("next wednesday", through=_THROUGH, today=_TODAY)
        assert result == {
            "status": "error",
            "message": (
                "'next wednesday' is not a date — pass the first day the "
                "athlete is out as YYYY-MM-DD."
            ),
        }

    def test_in_the_past_is_an_error(self) -> None:
        result = validate_from_date("2026-05-19", through=_THROUGH, today=_TODAY)
        assert result == {
            "status": "error",
            "message": (
                "'2026-05-19' is in the past — from_date is the first day "
                "the athlete is out, not a day already lived through."
            ),
        }

    def test_after_through_date_is_an_error(self) -> None:
        result = validate_from_date("2026-05-28", through=_THROUGH, today=_TODAY)
        assert result == {
            "status": "error",
            "message": (
                "'2026-05-28' is after through_date (2026-05-27) — the "
                "window can't start after it ends."
            ),
        }

    def test_after_through_date_wins_over_the_future_window_redirect(self) -> None:
        # A from_date this far out would also be "next week or later", but
        # the out-of-range check must fire first — not a curated redirect
        # into a tool that can't build a window past through_date either.
        result = validate_from_date("2026-06-15", through=_THROUGH, today=_TODAY)
        assert result is not None
        assert result["status"] == "error"


class TestFutureWindowRedirect:
    def test_later_this_week_suggests_skip_planned_session(self) -> None:
        # 2026-05-24 is this week's Sunday — still the current week.
        result = validate_from_date("2026-05-24", through=_THROUGH, today=_TODAY)
        assert result["status"] == "refused"
        assert result["error_code"] == "future_window"
        assert result["suggested_tool"] == "skip_planned_session"

    def test_next_week_or_later_suggests_set_plan_directives(self) -> None:
        # 2026-05-25 is next Monday.
        result = validate_from_date("2026-05-25", through=_THROUGH, today=_TODAY)
        assert result["status"] == "refused"
        assert result["error_code"] == "future_window"
        assert result["suggested_tool"] == "set_plan_directives"

    def test_message_names_the_window_and_both_alternative_tools(self) -> None:
        result = validate_from_date("2026-05-25", through=_THROUGH, today=_TODAY)
        assert result["message"] == (
            "pause_training always starts today. For 2026-05-25..2026-05-27: "
            'use set_plan_directives(unavailable=[["2026-05-25", '
            '"2026-05-27"]]) when the window starts next week or later, or '
            "skip_planned_session(session_id) for days later in "
            "the current week."
        )

    def test_a_sunday_today_has_no_more_days_left_in_this_week(self) -> None:
        sunday = date(2026, 5, 24)
        result = validate_from_date(
            "2026-05-25", through=date(2026, 6, 1), today=sunday
        )
        assert result["suggested_tool"] == "set_plan_directives"
