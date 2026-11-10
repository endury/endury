"""Athlete-profile storage contract.

Pins the rows of the retired ``docs/requirements/athlete.md`` that were
worth a mechanical check: the single-row ``CHECK (id = 1)`` and the
no-orphan-columns rule — every DB column is either a model
field or the one deliberate ``coach_personality`` orphan, pending
its migration.
"""

from __future__ import annotations

from endury.database.sqlite.session import DatabaseSession
from endury.domain.models.athlete import AthleteProfile

#: The personality feature is gone; the column waits for a migration.
_DELIBERATE_ORPHANS = {"coach_personality"}


def _columns(tmp_path) -> set[str]:
    session = DatabaseSession(str(tmp_path / "contract.db"))
    session.init_schema()
    with session.cursor() as cursor:
        cursor.execute("PRAGMA table_info(athlete_profile)")
        return {row[1] for row in cursor.fetchall()}


def test_every_column_is_a_model_field_or_a_documented_orphan(tmp_path):
    columns = _columns(tmp_path)
    model_fields = set(AthleteProfile.model_fields)
    orphans = columns - model_fields
    assert orphans == _DELIBERATE_ORPHANS
    # And the model never grows a field the table cannot store.
    assert model_fields <= columns


def test_the_table_refuses_a_second_athlete_row(tmp_path):
    import pytest

    from endury.common.exceptions import RepositoryError

    session = DatabaseSession(str(tmp_path / "contract.db"))
    session.init_schema()
    with session.cursor() as cursor:
        cursor.execute("SELECT id FROM athlete_profile")
        assert [row[0] for row in cursor.fetchall()] == [1]
    with pytest.raises(RepositoryError, match="CHECK"), session.cursor() as cursor:
        cursor.execute(
            "INSERT INTO athlete_profile (id, created_at, updated_at) "
            "VALUES (2, '2026-01-01', '2026-01-01')"
        )
