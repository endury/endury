"""Smoke checks for the retained devtool registry."""

from __future__ import annotations

from pathlib import Path


def test_makefile_has_no_llm_planner_devtools() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert "llm_generate_macro_templates" not in makefile
    assert "llm_eval_planner" not in makefile
    assert "llm_refine_plan" not in makefile
    assert "compile_macro_template_report" not in makefile
    assert "audit_templates" not in makefile
    assert "llm_review_activity_catalog" not in makefile
