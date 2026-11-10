"""Keep the pages people read in the register AGENTS.md asks for.

Coding agents write Endury's docs, and left alone they drift back to the
agent register: a table cell that carries a paragraph, a dated note
appended to a rule, a page that grows by accretion, a parenthesis inside a
parenthesis, a module table that names a file that is gone. This check
reads the human pages (the README, VISION.md, CONTRIBUTING.md,
docs/architecture.md, the CHANGELOG, the package READMEs and web/README.md)
and reports where one slipped. The pages written for agents and tests
(AGENTS.md, docs/specs/, the rulebook, the research) are not checked.

Run it with ``make lint`` or ``poetry run python -m devtools.core.check_docs``.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The named human pages and the most words of prose each may hold. Image
#: lines, table rows, comments and fenced code are not counted.
PAGE_WORDS: dict[str, int] = {
    "README.md": 1300,
    "VISION.md": 1800,
    "CONTRIBUTING.md": 1200,
    "docs/architecture.md": 2200,
}
#: The ceiling for a package README under ``endury/`` and for ``web/README.md``.
PACKAGE_README_WORDS = 1300
#: The most words a table cell may hold on a human page.
CELL_WORDS = 25
#: The most lines a CHANGELOG entry may run to; the detail is in git.
CHANGELOG_ENTRY_LINES = 6
#: Pages under ``docs/`` that carry dates by design: the review log, the
#: generated rule page, the research, and the specs' provenance notes.
DATED_NOTE_EXEMPT = (
    "docs/plan_maturity.md",
    "docs/quality/rules.md",
    "docs/research/",
    "docs/specs/",
)

_DATED_NOTE = re.compile(
    r"\b(?:fixed|added|changed|amended|until|since)\s+20\d\d-\d\d-\d\d", re.IGNORECASE
)
_INLINE_CODE = re.compile(r"`[^`]*`")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_NESTED_PARENS = re.compile(r"\([^()]*\(")
_MODULE_TOKEN = re.compile(r"`([\w./-]+\.py)`")


@dataclass(frozen=True)
class Violation:
    """One place a human page slipped out of register."""

    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        """Render as ``path:line: [rule] detail``."""
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _rel(path: Path, repo: Path) -> str:
    return path.relative_to(repo).as_posix()


def human_pages(repo: Path = REPO_ROOT) -> list[Path]:
    """The pages people read: the named pages, the package READMEs, web/README.md."""
    named = [repo / page for page in PAGE_WORDS]
    packages = [*sorted(repo.glob("endury/*/README.md")), repo / "endury/README.md"]
    return [p for p in [*named, *packages, repo / "web/README.md"] if p.is_file()]


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """Lines that count as prose: no fenced code, table rows, images or other HTML."""
    lines: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not stripped or stripped.startswith(("![", "<", "|")):
            continue
        lines.append((number, line))
    return lines


def check_page_words(repo: Path = REPO_ROOT) -> list[Violation]:
    """A human page stays under its word ceiling."""
    found: list[Violation] = []
    for page in human_pages(repo):
        rel = _rel(page, repo)
        ceiling = PAGE_WORDS.get(rel, PACKAGE_README_WORDS)
        words = sum(
            len(line.split()) for _, line in _prose_lines(page.read_text("utf-8"))
        )
        if words > ceiling:
            found.append(
                Violation(
                    rel, 1, "page-words", f"{words} words of prose, ceiling {ceiling}"
                )
            )
    return found


def check_table_cells(repo: Path = REPO_ROOT) -> list[Violation]:
    """A table cell on a human page is one short sentence."""
    found: list[Violation] = []
    for page in human_pages(repo):
        rel = _rel(page, repo)
        for number, line in enumerate(page.read_text("utf-8").splitlines(), start=1):
            if not line.lstrip().startswith("|"):
                continue
            for cell in line.strip().strip("|").split("|"):
                words = len(cell.split())
                if words > CELL_WORDS:
                    found.append(
                        Violation(
                            rel,
                            number,
                            "table-cell",
                            f"{words} words in one cell, limit {CELL_WORDS}",
                        )
                    )
    return found


def check_dated_notes(repo: Path = REPO_ROOT) -> list[Violation]:
    """No dated note on a human page or under docs/; the CHANGELOG and git hold history."""
    pages = set(human_pages(repo)) | set(repo.glob("docs/**/*.md"))
    found: list[Violation] = []
    for page in sorted(pages):
        rel = _rel(page, repo)
        if rel.startswith(DATED_NOTE_EXEMPT):
            continue
        for number, line in enumerate(page.read_text("utf-8").splitlines(), start=1):
            match = _DATED_NOTE.search(line)
            if match:
                found.append(
                    Violation(
                        rel,
                        number,
                        "dated-note",
                        f"{match.group(0)!r}: rewrite the paragraph instead",
                    )
                )
    return found


def check_nested_parentheses(repo: Path = REPO_ROOT) -> list[Violation]:
    """No parenthesis inside a parenthesis on a human page; links and inline code do not count."""
    found: list[Violation] = []
    for page in human_pages(repo):
        rel = _rel(page, repo)
        for number, line in _prose_lines(page.read_text("utf-8")):
            if _NESTED_PARENS.search(_LINK_TARGET.sub("]", _INLINE_CODE.sub("", line))):
                found.append(
                    Violation(rel, number, "nested-parentheses", line.strip()[:80])
                )
    return found


def check_changelog_entries(repo: Path = REPO_ROOT) -> list[Violation]:
    """A CHANGELOG entry runs to a few lines; the detail is in git."""
    page = repo / "CHANGELOG.md"
    if not page.is_file():
        return []
    entries: list[tuple[int, int]] = []
    start = 0
    for number, line in enumerate(page.read_text("utf-8").splitlines(), start=1):
        if line.startswith("- "):
            start = number
            entries.append((start, 1))
        elif start and line.startswith("  "):
            entries[-1] = (start, entries[-1][1] + 1)
        else:
            start = 0
    return [
        Violation(
            "CHANGELOG.md",
            first,
            "changelog-entry",
            f"{length} lines, limit {CHANGELOG_ENTRY_LINES}",
        )
        for first, length in entries
        if length > CHANGELOG_ENTRY_LINES
    ]


def check_module_tables(repo: Path = REPO_ROOT) -> list[Violation]:
    """A package README names every module of its package, and none that is gone."""
    found: list[Violation] = []
    for readme in sorted(repo.glob("endury/*/README.md")):
        package = readme.parent
        rel = _rel(readme, repo)
        text = readme.read_text("utf-8")
        for module in sorted(package.rglob("*.py")):
            if module.name == "__init__.py" or "__pycache__" in module.parts:
                continue
            relative = module.relative_to(package)
            parents = [
                f"{parent.as_posix()}/" for parent in relative.parents if parent.name
            ]
            if relative.as_posix() not in text and not any(
                parent in text for parent in parents
            ):
                found.append(
                    Violation(
                        rel, 1, "module-table", f"{relative.as_posix()} is not named"
                    )
                )
        for number, line in enumerate(text.splitlines(), start=1):
            found.extend(
                Violation(rel, number, "module-table", f"`{token}` does not exist")
                for token in _MODULE_TOKEN.findall(line)
                if not (package / token).is_file() and not (repo / token).is_file()
            )
    return found


CHECKS = (
    check_page_words,
    check_table_cells,
    check_dated_notes,
    check_nested_parentheses,
    check_changelog_entries,
    check_module_tables,
)


def run_checks(repo: Path = REPO_ROOT) -> list[Violation]:
    """Every violation on the human pages of ``repo``, in page order."""
    found = [violation for check in CHECKS for violation in check(repo)]
    return sorted(found, key=lambda v: (v.path, v.line, v.rule))


def main() -> int:
    """Print every violation and exit non-zero when there is one."""
    violations = run_checks()
    for violation in violations:
        print(violation)
    if violations:
        print(f"{len(violations)} docs violation(s)")
        return 1
    print("docs checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
