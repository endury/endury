"""Tests for the athlete-memory "## Notes" conventions."""

from datetime import date

from endury.common.memory_notes import (
    MEMORY_DOCUMENT_RULES,
    append_note,
    constraints,
    due_check_ins,
    due_reviews,
    undated_constraints,
)

REVIEWED = (
    "## Goals & races\n- Ridge Marathon\n\n"
    "## Constraints (injury, availability)\n"
    "- Sick, sore throat (review 2026-09-24)\n"
    "- Travel Oct 10-12 (review 2026-10-13)\n"
    "- Achilles niggle\n\n"
    "## Notes\n- 2026-09-20: felt flat (review 2026-01-01)"
)


def test_constraints_reads_only_the_constraints_heading():
    assert constraints(REVIEWED) == [
        "- Sick, sore throat (review 2026-09-24)",
        "- Travel Oct 10-12 (review 2026-10-13)",
        "- Achilles niggle",
    ]


def test_a_placeholder_bullet_is_not_a_constraint():
    document = "## Constraints (injury, availability)\n- None\n\n## Notes\n"
    assert constraints(document) == []


def test_a_constraint_without_a_review_date_is_undated():
    assert undated_constraints(REVIEWED) == ["- Achilles niggle"]


def test_a_constraint_is_due_on_its_review_date():
    due = due_reviews(REVIEWED, date(2026, 9, 24), written=date(2026, 9, 20))
    assert due == ["- Sick, sore throat (review 2026-09-24)"]


def test_an_undated_constraint_is_due_two_weeks_after_the_last_write():
    assert "- Achilles niggle" not in due_reviews(
        REVIEWED, date(2026, 10, 3), written=date(2026, 9, 20)
    )
    assert "- Achilles niggle" in due_reviews(
        REVIEWED, date(2026, 10, 4), written=date(2026, 9, 20)
    )


def test_an_undated_constraint_of_unknown_age_is_due_at_once():
    assert due_reviews(REVIEWED, date(2026, 9, 1), written=None) == [
        "- Achilles niggle"
    ]


def test_document_rules_teach_the_review_date():
    assert "(review YYYY-MM-DD)" in MEMORY_DOCUMENT_RULES
    assert "only when the athlete says" not in MEMORY_DOCUMENT_RULES


def test_document_rules_point_races_at_the_race_table():
    assert "upsert_race" in MEMORY_DOCUMENT_RULES
    assert "race table" in MEMORY_DOCUMENT_RULES


def test_append_note_adds_the_bullet_under_an_existing_notes_heading():
    document = "## Goals & races\n- 2027-06-12 Roth\n\n## Notes\n- likes hills\n"
    updated = append_note(document, "- 2026-09-14: committed plan", cap=6000)
    assert updated.endswith("- likes hills\n- 2026-09-14: committed plan")
    assert "## Goals & races" in updated


def test_append_note_creates_the_notes_heading_when_absent():
    updated = append_note("## Preferences\n- mornings", "- 2026-09-14: x", cap=6000)
    assert updated == "## Preferences\n- mornings\n\n## Notes\n- 2026-09-14: x"


def test_append_note_starts_a_document_from_nothing():
    assert append_note("", "- 2026-09-14: x", cap=6000) == "## Notes\n- 2026-09-14: x"


def test_append_note_inserts_before_a_following_heading():
    document = "## Notes\n- old\n\n## Preferences\n- mornings"
    updated = append_note(document, "- new", cap=6000)
    assert updated == "## Notes\n- old\n- new\n\n## Preferences\n- mornings"


def test_append_note_drops_the_oldest_bullets_until_it_fits():
    document = "## Notes\n- oldest\n- middle\n- newest"
    updated = append_note(document, "- brand new", cap=len("## Notes\n") + 30)
    assert "- oldest" not in updated
    assert "- newest" in updated
    assert updated.endswith("- brand new")


def test_append_note_keeps_prose_and_drops_only_bullets():
    document = "## Notes\nkeep this prose\n- oldest\n- newest"
    updated = append_note(document, "- brand new", cap=len("## Notes\n") + 38)
    assert "keep this prose" in updated
    assert "- oldest" not in updated


def test_append_note_returns_the_document_unchanged_when_it_cannot_fit():
    document = "## Notes\n- old"
    assert append_note(document, "- far too long to ever fit", cap=20) == document


def test_due_check_ins_returns_lines_whose_date_has_arrived():
    document = (
        "## Notes\n"
        "- Next check-in 2026-03-01: pick a spring race\n"
        "- Next check-in 2027-01-01: revisit the bike fit\n"
    )
    assert due_check_ins(document, date(2026, 9, 14)) == [
        (date(2026, 3, 1), "pick a spring race")
    ]


def test_due_check_ins_includes_a_line_dated_today():
    document = "- Next check-in 2026-09-14: race entry closes"
    assert due_check_ins(document, date(2026, 9, 14)) == [
        (date(2026, 9, 14), "race entry closes")
    ]


def test_due_check_ins_ignores_other_bullets_and_impossible_dates():
    document = "- 2026-01-01: a normal note\n- Next check-in 2026-13-45: nonsense\n"
    assert due_check_ins(document, date(2026, 9, 14)) == []
