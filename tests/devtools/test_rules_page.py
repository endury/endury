"""The generated rule page is current and shows every rule of the rulebook."""

from __future__ import annotations

from bench.rules import load_rulebook
from devtools.quality.rules_page import PAGE, render


def test_the_committed_rule_page_is_what_the_rulebook_generates() -> None:
    assert PAGE.read_text(encoding="utf-8") == render(load_rulebook()), (
        "docs/quality/rules.md is stale: run `make docs` and commit the result"
    )


def test_the_page_shows_every_rule_once_and_keeps_its_tables_whole() -> None:
    rulebook = load_rulebook()
    page = render(rulebook)
    for rule in rulebook.rules:
        assert page.count(f"| `{rule.name}` |") == 1, rule.name  # its table row
        assert f"<summary><code>{rule.name}</code>" in page, rule.name  # its bands
    # A pipe inside a rule's text must not open a new table column.
    piped = [rule for rule in rulebook.rules if "|" in rule.reads]
    assert piped
    for rule in piped:
        assert rule.reads.replace("|", "\\|") in page, rule.name
