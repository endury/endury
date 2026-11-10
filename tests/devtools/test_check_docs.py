"""The docs check reports each way a human page slips out of register, and the repository passes it."""

from pathlib import Path

import pytest

from devtools.core.check_docs import (
    CELL_WORDS,
    CHANGELOG_ENTRY_LINES,
    PACKAGE_README_WORDS,
    check_changelog_entries,
    check_dated_notes,
    check_module_tables,
    check_nested_parentheses,
    check_page_words,
    check_table_cells,
    run_checks,
)


def _repo(tmp_path: Path, **files: str) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _rules(violations) -> list[str]:
    return [v.rule for v in violations]


def test_repository_passes() -> None:
    assert run_checks() == []


def test_page_words_counts_prose_only(tmp_path: Path) -> None:
    prose = "word " * (PACKAGE_README_WORDS - 1) + "\n"
    noise = (
        '![an image](x.png)\n<picture><source srcset="x_dark.svg"></picture>\n'
        "| a | table | row |\n```\ncode code code\n```\n"
    )
    ok = _repo(tmp_path, **{"web/README.md": prose + noise})
    assert check_page_words(ok) == []
    over = _repo(tmp_path, **{"web/README.md": "word " * (PACKAGE_README_WORDS + 1)})
    assert _rules(check_page_words(over)) == ["page-words"]


def test_table_cells_are_short(tmp_path: Path) -> None:
    short = "| " + "w " * CELL_WORDS + "| b |\n"
    assert check_table_cells(_repo(tmp_path, **{"web/README.md": short})) == []
    long = "| " + "w " * (CELL_WORDS + 1) + "| b |\n"
    found = check_table_cells(_repo(tmp_path, **{"web/README.md": long}))
    assert _rules(found) == ["table-cell"]
    assert found[0].line == 1


@pytest.mark.parametrize(
    "text",
    [
        "*Fixed 2026-09-25: it did X.*",
        "since 2026-10-11 the test",
        "Until 2026-09-24 the seed",
    ],
)
def test_dated_notes_are_reported(tmp_path: Path, text: str) -> None:
    found = check_dated_notes(_repo(tmp_path, **{"docs/privacy.md": text}))
    assert _rules(found) == ["dated-note"]


def test_dated_notes_allow_the_review_log_and_the_specs(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        **{
            "docs/plan_maturity.md": "Reviewed since 2026-10-01.",
            "docs/specs/planner/x.md": "*Until 2026-09-25 the band was absolute.*",
            "docs/quality/README.md": "Applied by the maintainer on 2026-10-04.",
        },
    )
    assert check_dated_notes(repo) == []


def test_nested_parentheses(tmp_path: Path) -> None:
    fine = "One (aside), a link (see [x](a(b).md)) and code (`f(g(x))`).\n| (a (b)) in a table is not prose |\n"
    assert check_nested_parentheses(_repo(tmp_path, **{"web/README.md": fine})) == []
    nested = "The rule (its band (measured) and source) holds.\n"
    assert _rules(
        check_nested_parentheses(_repo(tmp_path, **{"web/README.md": nested}))
    ) == ["nested-parentheses"]


def test_changelog_entries_stay_short(tmp_path: Path) -> None:
    entry = "- first line\n" + "  more\n" * (CHANGELOG_ENTRY_LINES - 1)
    assert (
        check_changelog_entries(_repo(tmp_path, **{"CHANGELOG.md": "## x\n\n" + entry}))
        == []
    )
    long = "- first line\n" + "  more\n" * CHANGELOG_ENTRY_LINES + "\n- next\n"
    found = check_changelog_entries(_repo(tmp_path, **{"CHANGELOG.md": long}))
    assert _rules(found) == ["changelog-entry"]
    assert found[0].line == 1


def test_module_tables_match_the_package(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        **{
            "endury/pkg/README.md": "| `here.py` | x |\n| `sub/` | y |\n| `gone.py` | z |\n",
            "endury/pkg/here.py": "",
            "endury/pkg/missing.py": "",
            "endury/pkg/sub/deep.py": "",
            "endury/pkg/__init__.py": "",
        },
    )
    details = sorted(v.detail for v in check_module_tables(repo))
    assert details == ["`gone.py` does not exist", "missing.py is not named"]
