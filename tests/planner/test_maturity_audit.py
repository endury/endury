"""The maturity audit: every goal's label is exactly what the repo's evidence supports.

``PlanMaturity`` names the evidence each tier needs. This audit reads that
evidence where it lives — the rulebook's bands, the scorecard's committed
matrix baseline, the fingerprint scenarios, the maintenance convergence
test, and the menu-review and completed-preparation logs in
``docs/plan_maturity.md`` — and
fails both ways: a label that claims more than its evidence, and a label
that claims less.

``devtools`` is imported here, never from ``endury``: the planner must not
learn about its own instruments.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import tests.planner.test_maintenance as maintenance_tests
from bench.rules import load_rulebook
from devtools.planner._shared.scenarios import SCENARIOS
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PlanMaturity,
    is_maintenance_goal,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.sessions.menus import BLUEPRINTS, get_session_menu

_ROOT = Path(__file__).resolve().parents[2]
_MATRIX_BASELINE = _ROOT / "devtools/quality/baselines/planner_matrix.json"
_BETA_LOG = _ROOT / "docs/plan_maturity.md"
_LOG_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}) ([a-z0-9_]+): \S")
_REVIEW_LINE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2}) ([a-z0-9_]+) (basic|moderate|competitive)"
    r"(?: \[menu ([0-9a-f]{12})\])?: \S"
)
_TIERS = frozenset(AmbitionLevel)


@dataclass(frozen=True)
class Evidence:
    """What the repository holds for one goal.

    Attributes:
        own_bands: Bands the rulebook holds for the goal itself that
            rest on at least one measured source.
        matrix_tiers: Tiers with a committed scorecard-matrix cell.
        scenario_tiers: Tiers with a committed fingerprint scenario.
        converges: The maintenance convergence test covers the goal.
        reviewed_tiers: Tiers whose menus carry a dated review line.
        completed: Dated completed-preparation lines in the log.
    """

    own_bands: int = 0
    matrix_tiers: frozenset[AmbitionLevel] = frozenset()
    scenario_tiers: frozenset[AmbitionLevel] = frozenset()
    converges: bool = False
    reviewed_tiers: frozenset[AmbitionLevel] = frozenset()
    completed: int = 0


def _missing_for_alpha(
    maintenance: bool, evidence: Evidence, offered: frozenset[AmbitionLevel]
) -> list[str]:
    """What stands between the goal and Alpha; empty when it has earned it.

    The evidence is asked of the tiers the goal's menu offers: a label
    speaks for the plans an athlete can build.
    """
    if maintenance:
        missing = [
            f"a committed {tier.value} scenario"
            for tier in sorted(offered - evidence.scenario_tiers)
        ]
        if not evidence.converges:
            missing.append("the maintenance convergence test")
    else:
        missing = []
        if not evidence.own_bands:
            missing.append("a band of its own in the rulebook")
        missing.extend(
            f"a scorecard matrix cell for {tier.value}"
            for tier in sorted(offered - evidence.matrix_tiers)
        )
    missing.extend(
        f"a menu review for {tier.value}"
        for tier in sorted(offered - evidence.reviewed_tiers)
    )
    return missing


def audit(
    goal: str,
    label: PlanMaturity,
    maintenance: bool,
    evidence: Evidence,
    offered: frozenset[AmbitionLevel] = _TIERS,
) -> str:
    """Return why ``label`` misstates the evidence, or an empty string.

    A goal that offers no tier is not audited: nobody can build its plan,
    so its label waits with its menu until a tier is back on offer.
    """
    if not offered:
        return ""
    missing = _missing_for_alpha(maintenance, evidence, offered)
    if not missing:
        supported = PlanMaturity.BETA if evidence.completed else PlanMaturity.ALPHA
    else:
        supported = PlanMaturity.EXPERIMENTAL
    if label is supported:
        return ""
    rank = [PlanMaturity.EXPERIMENTAL, PlanMaturity.ALPHA, PlanMaturity.BETA]
    if rank.index(label) > rank.index(supported):
        needs = missing or ["a completed preparation in docs/plan_maturity.md"]
        return f"{goal} is labelled {label.value} but lacks {'; '.join(needs)}"
    surplus = (
        "a completed preparation in docs/plan_maturity.md"
        if supported is PlanMaturity.BETA
        else "the evidence for alpha"
    )
    return (
        f"{goal} is labelled {label.value} but holds {surplus}: "
        f"relabel it {supported.value}"
    )


def _evidence(goal: GoalType) -> Evidence:
    """Collect one goal's evidence from the repository."""
    own = [
        rule.band_spec.get("by_goal", {}).get(goal.value)
        for rule in load_rulebook().rules
    ]
    cells = json.loads(_MATRIX_BASELINE.read_text())["cells"]
    lines = _BETA_LOG.read_text().splitlines()
    completed = [match.group(2) for line in lines if (match := _LOG_LINE.match(line))]
    reviews = [
        match.group(2, 3) for line in lines if (match := _REVIEW_LINE.match(line))
    ]
    return Evidence(
        own_bands=sum(1 for band in own if band is not None and "why" in band),
        matrix_tiers=frozenset(
            tier for tier in AmbitionLevel if f"{goal.value}_{tier.value}" in cells
        ),
        scenario_tiers=frozenset(
            scenario.ambition
            for scenario in SCENARIOS.values()
            if scenario.goals and scenario.goals[0].goal_type is goal
        ),
        converges=goal in maintenance_tests._MAINTENANCE_GOALS,
        reviewed_tiers=frozenset(
            AmbitionLevel(tier) for name, tier in reviews if name == goal.value
        ),
        completed=completed.count(goal.value),
    )


def test_every_goal_is_labelled_what_its_evidence_supports():
    problems = [
        audit(
            goal.value,
            profile.maturity,
            is_maintenance_goal(goal),
            _evidence(goal),
            BLUEPRINTS[goal].offered,
        )
        for goal, profile in GOAL_PROFILES.items()
    ]
    assert [p for p in problems if p] == []


def test_every_log_line_names_a_goal():
    goals = {goal.value for goal in GoalType}
    for line in _BETA_LOG.read_text().splitlines():
        if match := _LOG_LINE.match(line) or _REVIEW_LINE.match(line):
            assert match.group(2) in goals, line


_FULL_RACE = Evidence(own_bands=1, matrix_tiers=_TIERS, reviewed_tiers=_TIERS)


def test_a_label_that_outruns_its_evidence_fails():
    """A made-up goal with one band but no matrix cell claims Alpha."""
    verdict = audit(
        "made_up_goal",
        PlanMaturity.ALPHA,
        False,
        Evidence(
            own_bands=1,
            matrix_tiers=frozenset({AmbitionLevel.BASIC}),
            reviewed_tiers=_TIERS,
        ),
    )
    assert verdict == (
        "made_up_goal is labelled alpha but lacks a scorecard matrix cell for "
        "competitive; a scorecard matrix cell for moderate"
    )


def test_the_evidence_is_asked_of_the_tiers_on_offer():
    """One offered tier, graded and reviewed, is Alpha; nothing on offer is not audited."""
    competitive = frozenset({AmbitionLevel.COMPETITIVE})
    one_tier = Evidence(
        own_bands=1, matrix_tiers=competitive, reviewed_tiers=competitive
    )
    assert audit("made_up_goal", PlanMaturity.ALPHA, False, one_tier, competitive) == ""
    assert (
        audit("made_up_goal", PlanMaturity.ALPHA, False, Evidence(), frozenset()) == ""
    )


def test_alpha_needs_every_tiers_menus_reviewed():
    """Scorecard evidence alone does not make a goal Alpha."""
    one_review = Evidence(
        own_bands=1,
        matrix_tiers=_TIERS,
        reviewed_tiers=frozenset({AmbitionLevel.COMPETITIVE}),
    )
    assert audit("made_up_goal", PlanMaturity.ALPHA, False, one_review) == (
        "made_up_goal is labelled alpha but lacks a menu review for basic; "
        "a menu review for moderate"
    )
    assert audit("made_up_goal", PlanMaturity.EXPERIMENTAL, False, one_review) == ""


def test_a_matrix_cell_without_a_measured_band_is_not_alpha():
    """The 10K's cells are a regression guard graded on shared bands."""
    verdict = audit(
        "made_up_goal", PlanMaturity.ALPHA, False, Evidence(matrix_tiers=_TIERS)
    )
    assert "lacks a band" in verdict


def test_beta_needs_a_completed_preparation():
    verdict = audit("made_up_goal", PlanMaturity.BETA, False, _FULL_RACE)
    assert verdict == (
        "made_up_goal is labelled beta but lacks a completed preparation in "
        "docs/plan_maturity.md"
    )


def test_a_label_that_undersells_its_evidence_fails():
    verdict = audit("made_up_goal", PlanMaturity.EXPERIMENTAL, False, _FULL_RACE)
    assert verdict == (
        "made_up_goal is labelled experimental but holds the evidence for "
        "alpha: relabel it alpha"
    )
    logged = Evidence(
        own_bands=1, matrix_tiers=_TIERS, reviewed_tiers=_TIERS, completed=1
    )
    assert audit("made_up_goal", PlanMaturity.ALPHA, False, logged) == (
        "made_up_goal is labelled alpha but holds a completed preparation in "
        "docs/plan_maturity.md: relabel it beta"
    )


def test_a_maintenance_block_needs_a_scenario_per_tier_and_convergence():
    one_tier = Evidence(
        scenario_tiers=frozenset({AmbitionLevel.BASIC}),
        converges=True,
        reviewed_tiers=_TIERS,
    )
    assert audit("made_up_block", PlanMaturity.ALPHA, True, one_tier) == (
        "made_up_block is labelled alpha but lacks a committed competitive "
        "scenario; a committed moderate scenario"
    )
    no_test = Evidence(scenario_tiers=_TIERS, reviewed_tiers=_TIERS)
    assert "lacks the maintenance convergence test" in audit(
        "made_up_block", PlanMaturity.ALPHA, True, no_test
    )
    unreviewed = Evidence(scenario_tiers=_TIERS, converges=True)
    assert "lacks a menu review for basic" in audit(
        "made_up_block", PlanMaturity.ALPHA, True, unreviewed
    )
    full = Evidence(scenario_tiers=_TIERS, converges=True, reviewed_tiers=_TIERS)
    assert audit("made_up_block", PlanMaturity.ALPHA, True, full) == ""


def menu_fingerprint(goal: GoalType, tier: AmbitionLevel) -> str:
    """Return the short hash of every session one goal's tier prescribes.

    A spec's ``load_weeks`` counts only when it is set, so the menus
    reviewed before the field existed keep their hashes.
    """
    specs = "\n".join(
        f"{phase.value}/{mode_key}: {spec!r}".replace(", load_weeks=False)", ")")
        for phase, mode_key in BLUEPRINTS[goal].phases
        for spec in get_session_menu(goal, phase, mode_key, tier)
    )
    return hashlib.sha256(specs.encode()).hexdigest()[:12]


def test_every_tier_on_offer_was_reviewed_as_it_is():
    """A tier on offer whose sessions changed since its last review is unreviewed.

    The last review line of a goal and tier carries ``[menu <hash>]``, the
    fingerprint of the sessions the maintainer reviewed. After a menu change,
    review the tier again and add a dated line with the hash this test prints.
    """
    stamps: dict[tuple[str, str], str | None] = {}
    for line in _BETA_LOG.read_text().splitlines():
        if match := _REVIEW_LINE.match(line):
            stamps[match.group(2, 3)] = match.group(4)
    stale = [
        f"{goal.value} {tier.value} [menu {menu_fingerprint(goal, tier)}]"
        for goal, blueprint in BLUEPRINTS.items()
        for tier in blueprint.offered
        if stamps.get((goal.value, tier.value)) != menu_fingerprint(goal, tier)
    ]
    assert stale == []
