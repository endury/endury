"""Tests for profile tools: athlete profile and the athlete-memory document."""

import json

import pytest

from endury.common.exceptions import RepositoryError
from endury.common.memory_notes import MEMORY_DOCUMENT_RULES
from endury.database.sqlite.repositories.athlete_memory import MEMORY_CHAR_CAP


def test_get_athlete_profile_tool(coach_tools):
    tool = next(t for t in coach_tools if t.name == "get_athlete_profile")
    result = tool.invoke({})
    payload = json.loads(result)

    assert payload["name"] == "Alex"
    assert payload["primary_sport"] == "running"


def test_update_athlete_profile_no_fields(coach_tools):
    tool = next(t for t in coach_tools if t.name == "update_athlete_profile")
    result = tool.invoke({})
    payload = json.loads(result)

    assert payload["success"] is False
    assert "No fields" in payload["message"]


def _memory_tool(coach_tools):
    """Return the update_memory tool from the built tool list."""
    return next(t for t in coach_tools if t.name == "update_memory")


def test_update_memory_replaces_the_whole_document(coach_tools, mock_memory_repo):
    document = "## Goals & races\n- 2026-10-04 Berlin Marathon"
    payload = json.loads(_memory_tool(coach_tools).invoke({"content": document}))

    assert payload["success"] is True
    assert payload["characters"] == len(document)
    mock_memory_repo.set.assert_called_once_with(document)


def test_update_memory_reports_an_unchanged_document_as_unchanged(
    coach_tools, mock_memory_repo
):
    """A rewrite the repository no-opped must not read as a fresh version."""
    mock_memory_repo.set.return_value = False

    payload = json.loads(_memory_tool(coach_tools).invoke({"content": "same"}))

    assert payload["success"] is True
    assert "unchanged" in payload["message"].lower()


def test_update_athlete_profile_docstring_guards_the_stated_fields(coach_tools):
    """The app pitches a default experience level every turn; the model must
    never write that pitch back as the athlete's own answer."""
    body = next(
        t for t in coach_tools if t.name == "update_athlete_profile"
    ).description
    experience = body.partition("experience_level:")[2].partition("primary_sport:")[0]
    sport = body.partition("primary_sport:")[2].partition("communication_style:")[0]
    assert "never infer" in experience
    assert "never infer" in sport


def test_update_memory_rejects_a_document_over_the_cap(coach_tools, mock_memory_repo):
    """The repository's cap surfaces as a refusal the model can act on."""
    mock_memory_repo.set.side_effect = ValueError("too long — condense it")

    payload = json.loads(_memory_tool(coach_tools).invoke({"content": "x" * 9000}))

    assert payload["success"] is False
    assert "condense" in payload["message"]


def test_update_memory_docstring_states_the_enforced_cap(coach_tools):
    """The contract the model reads must quote the cap the code enforces."""
    assert str(MEMORY_CHAR_CAP) in _memory_tool(coach_tools).description


def test_update_athlete_profile_docstring_guards_the_name_field(coach_tools):
    """The tool description is the only thing stopping the model from
    writing a chip label like "Plan a workout" into the athlete's name."""
    tool = next(t for t in coach_tools if t.name == "update_athlete_profile")
    assert "never infer it" in tool.description


def test_update_memory_repository_error(coach_tools, mock_memory_repo):
    mock_memory_repo.set.side_effect = RepositoryError("DB error")

    payload = json.loads(_memory_tool(coach_tools).invoke({"content": "note"}))

    assert "error" in payload
    assert "database" in payload["message"].lower()


def test_get_athlete_profile_no_profile_returns_message(coach_tools, mock_athlete_repo):
    """No stored profile → a setup message instead of an empty dict."""
    mock_athlete_repo.get.return_value = None

    tool = next(t for t in coach_tools if t.name == "get_athlete_profile")
    payload = json.loads(tool.invoke({}))

    assert "message" in payload


def test_get_athlete_profile_exception_path(coach_tools, mock_athlete_repo):
    """An unexpected exception while reading the profile still returns an error payload."""
    mock_athlete_repo.get.side_effect = RuntimeError("DB error")

    tool = next(t for t in coach_tools if t.name == "get_athlete_profile")
    result = tool.invoke({})
    payload = json.loads(result)

    assert "error" in payload


# update_athlete_profile — additional fields


def test_update_athlete_profile_all_fields(coach_tools):
    """Every updatable profile field is written in one call."""
    tool = next(t for t in coach_tools if t.name == "update_athlete_profile")
    result = tool.invoke(
        {
            "name": "Sam",
            "experience_level": "advanced",
            "primary_sport": "triathlon",
            "communication_style": "detailed",
        }
    )
    payload = json.loads(result)

    assert payload["success"] is True


def test_update_athlete_profile_preference_fields(coach_tools):
    """The optional preference fields write successfully."""
    tool = next(t for t in coach_tools if t.name == "update_athlete_profile")
    result = tool.invoke(
        {
            "preferred_run_surface": "trail",
            "open_water_access": True,
            "race_location_access": False,
        }
    )
    payload = json.loads(result)

    assert payload["success"] is True


def test_update_athlete_profile_unexpected_exception_propagates(
    coach_tools, mock_athlete_repo
):
    """A rejected write (RepositoryError) is the only failure swallowed here.

    Narrower than the other tools' blanket ``except Exception`` — an
    unrelated bug must never be laundered into the same generic "database
    error" message a real rejected write gets.
    """
    mock_athlete_repo.update.side_effect = RuntimeError("boom")

    tool = next(t for t in coach_tools if t.name == "update_athlete_profile")
    with pytest.raises(RuntimeError):
        tool.invoke({"name": "Alex"})


# RepositoryError-typed catch block tests


def test_get_athlete_profile_repository_error(coach_tools, mock_athlete_repo):
    """A RepositoryError while reading the profile surfaces as a database error message."""
    mock_athlete_repo.get.side_effect = RepositoryError("DB error")

    tool = next(t for t in coach_tools if t.name == "get_athlete_profile")
    result = tool.invoke({})
    payload = json.loads(result)

    assert "error" in payload
    assert "database" in payload["message"].lower()


def test_update_athlete_profile_repository_error(coach_tools, mock_athlete_repo):
    """A RepositoryError while updating the profile surfaces as a database error message."""
    mock_athlete_repo.update.side_effect = RepositoryError("DB error")

    tool = next(t for t in coach_tools if t.name == "update_athlete_profile")
    result = tool.invoke({"name": "Alex"})
    payload = json.loads(result)

    assert "error" in payload
    assert "database" in payload["message"].lower()


def test_update_memory_docstring_teaches_the_check_in_convention(coach_tools):
    """A "remind me in March" request only works if the model writes the line."""
    description = _memory_tool(coach_tools).description
    assert "Next check-in YYYY-MM-DD" in description


def test_update_memory_description_carries_the_shared_document_rules(coach_tools):
    """The registered tool description must quote MEMORY_DOCUMENT_RULES
    verbatim, so the coach and the "Tidy up" cleanup prompt never drift."""
    assert MEMORY_DOCUMENT_RULES in _memory_tool(coach_tools).description


def test_update_memory_docstring_teaches_review_dates(coach_tools):
    """A constraint is re-confirmed at its review date, never kept forever."""
    description = _memory_tool(coach_tools).description
    assert "(review YYYY-MM-DD)" in description
    assert "without being asked" not in description


def test_update_memory_refuses_a_constraint_without_a_review_date(
    coach_tools, mock_memory_repo
):
    document = "## Constraints (injury, availability)\n- Sick, sore throat\n"
    result = json.loads(_memory_tool(coach_tools).invoke({"content": document}))
    assert result["success"] is False
    assert "- Sick, sore throat" in result["message"]
    mock_memory_repo.set.assert_not_called()
