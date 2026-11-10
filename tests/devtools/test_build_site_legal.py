"""The legal pages name their host in the built site, never in the repository."""

from __future__ import annotations

import json
from pathlib import Path

from devtools.site.build_site import (
    LEGAL_FIELDS,
    LEGAL_PAGES,
    WEB_DIR,
    _fill_legal_pages,
)

_DETAILS = {
    "full_name": "Sam Example",
    "street": "Testweg 1",
    "city": "12345 M\u00fcnster & Hof",
    "email": "sam@example.com",
}


def _page(tmp_path: Path) -> Path:
    page = tmp_path / "impressum.html"
    marks = "".join(f'<span class="legal-fill">{mark}</span>' for mark in LEGAL_FIELDS)
    page.write_text(marks, encoding="utf-8")
    return page


def test_every_mark_is_filled_and_escaped(tmp_path: Path) -> None:
    page = _page(tmp_path)
    details = tmp_path / "legal.json"
    details.write_text(json.dumps(_DETAILS), encoding="utf-8")

    _fill_legal_pages([page], details)

    text = page.read_text(encoding="utf-8")
    assert "legal-fill" not in text
    assert "Sam Example" in text
    assert "12345 M\u00fcnster &amp; Hof" in text


def test_no_details_file_leaves_the_pages_blank(tmp_path: Path) -> None:
    page = _page(tmp_path)
    _fill_legal_pages([page], tmp_path / "missing.json")
    assert "legal-fill" in page.read_text(encoding="utf-8")


def test_the_committed_pages_hold_marks_not_a_host() -> None:
    for name in LEGAL_PAGES:
        text = (WEB_DIR / name).read_text(encoding="utf-8")
        for mark in LEGAL_FIELDS:
            assert f'<span class="legal-fill">{mark}</span>' in text
