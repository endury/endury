"""Pydantic request models for the planner RPC methods."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    field_validator,
    model_validator,
)

from endury.planning.wire.types import (
    JsonObject,
    MacroPlanPayload,
    ProjectionsPayload,
    SerializedSessionPayload,
    WeeklySummaryPayload,
)

#: Sports the self-assessment questionnaire accepts (mirrors the
#: ``athlete_self_assessment.sport`` CHECK constraint).
_SELF_ASSESSMENT_SPORTS = frozenset({"running", "cycling", "swimming"})


class RaceGoalModel(BaseModel):
    """One race in a plan request."""

    goal_type: str
    race_date: str
    priority: Literal["A", "B", "C"] = "A"


class GenerateRequest(BaseModel):
    """Params of the ``generate_plan`` RPC method.

    Athlete state is derived from intervals.icu activity history; day layout
    is authored by the session menus — neither is a client input.
    ``accept_short_horizon`` acknowledges the confirmation dialog a race
    closer than its goal's minimum horizon triggers: the plan is then built
    at full length and joined part-way through.
    ``accept_experimental`` acknowledges the confirmation an Experimental
    plan triggers (``experimental_plan``); it is never stored with the plan.
    ``weekly_hours_cap`` is the volume the athlete asked to stay under —
    a soft macro-QP target, so session floors still win over it.
    ``directives`` carries deterministic per-plan constraints (resume_date,
    suppress_tests_until, unavailable, weekly_hours_cap) applied by
    :func:`endury.planning.plan_directives.apply_directives`; opaque here
    and validated there.
    """

    races: list[RaceGoalModel]
    training_start: str
    ambition_level: Literal["basic", "moderate", "competitive"] = "moderate"
    accept_short_horizon: bool = False
    accept_experimental: bool = False
    weekly_hours_cap: Annotated[float, Field(gt=0)] | None = None
    directives: dict[str, Any] | None = None


class GenerateSuccess(BaseModel):
    """Successful plan generation response.

    ``maturity`` is the plan's effective tier (``experimental``, ``alpha``
    or ``beta``); an Experimental plan names why in ``maturity_reasons`` and
    carries the athlete-facing ``maturity_note`` as its caveat.
    """

    model_config = ConfigDict(extra="ignore")

    status: Literal["ok"] = "ok"
    sessions: list[SerializedSessionPayload] = Field(default_factory=list)
    weekly_summary: list[WeeklySummaryPayload] = Field(default_factory=list)
    phase_calendar: list[str] = Field(default_factory=list)
    coach_brief: str = ""
    warning: str | None = None
    projections: ProjectionsPayload | JsonObject = Field(default_factory=dict)
    macro_plan: MacroPlanPayload | JsonObject = Field(default_factory=dict)
    athlete_state: JsonObject | None = None
    maturity: str = ""
    maturity_reasons: list[str] = Field(default_factory=list)
    maturity_note: str | None = None
    solver_version: str = ""
    input_hash: str = ""


class GenerateError(BaseModel):
    """Failed plan generation response.

    ``error_code`` lets the client branch on a machine-readable reason:
    ``athlete_data_required``, which the wizard turns into the
    self-assessment questionnaire for ``missing_sports``, and
    ``horizon_too_short``, the confirm-gated refusal of a race closer than
    its goal's minimum horizon — the wizard turns it into the short-prep
    dialog and retries with ``accept_short_horizon``, reading the horizon
    it names off ``min_weeks``/``actual_weeks``/``deficit_weeks``; and
    ``experimental_plan``, the confirm-gated refusal of an Experimental plan
    — the wizard asks the athlete with ``error_message`` and retries with
    ``accept_experimental``, ``reasons`` naming why the plan is Experimental.
    ``plan_not_offered`` refuses a goal and tier no menu offers; its
    ``error_message`` names the plans on offer.
    """

    model_config = ConfigDict(extra="ignore")

    status: Literal["error"] = "error"
    error_message: str = ""
    error_code: str | None = None
    missing_sports: list[str] = Field(default_factory=list)
    min_weeks: int | None = None
    actual_weeks: int | None = None
    deficit_weeks: int | None = None
    reasons: list[str] = Field(default_factory=list)
    solver_version: str = ""
    input_hash: str = ""


# Discriminated union: Pydantic selects the model based on the ``status`` field.
GenerateResponse = Annotated[
    Annotated[GenerateSuccess, Tag("ok")] | Annotated[GenerateError, Tag("error")],
    Discriminator("status"),
]


class CommitRequest(BaseModel):
    """Params of the ``planner_commit`` RPC method.

    Requires the cached ``generate_result`` from the preview — the
    committed plan is exactly what the athlete previewed; the
    ``generate_request`` is persisted alongside for replanning.
    """

    name: str
    generate_request: GenerateRequest
    generate_result: GenerateResponse | None = None


class SelfAssessmentEntry(BaseModel):
    """One sport's self-reported recent training volume."""

    weekly_hours: float = Field(ge=0, le=40)
    longest_session_min: int = Field(ge=0, le=600)


class SelfAssessmentRequest(BaseModel):
    """Params of the ``put_self_assessment`` RPC method."""

    sports: dict[str, SelfAssessmentEntry]

    @field_validator("sports")
    @classmethod
    def _known_sports(
        cls, value: dict[str, SelfAssessmentEntry]
    ) -> dict[str, SelfAssessmentEntry]:
        """Reject empty submissions and sports the planner cannot plan."""
        if not value:
            raise ValueError("Answer at least one sport to continue.")
        unknown = sorted(set(value) - _SELF_ASSESSMENT_SPORTS)
        if unknown:
            raise ValueError(
                f"Unknown sport(s): {', '.join(unknown)}. "
                f"Use one of: {', '.join(sorted(_SELF_ASSESSMENT_SPORTS))}."
            )
        return value


#: The thresholds each sport's edit may set: the ones the plan's targets use.
_THRESHOLD_FIELDS: dict[str, frozenset[str]] = {
    "running": frozenset({"lthr_bpm", "threshold_pace_sec_per_km"}),
    "cycling": frozenset({"lthr_bpm", "ftp_watts"}),
    "swimming": frozenset({"css_sec_per_100m"}),
}
_THRESHOLD_FIELDS_ALL = frozenset().union(*_THRESHOLD_FIELDS.values())


class ThresholdsRequest(BaseModel):
    """Params of the ``put_thresholds`` RPC method.

    A value left out stays as it is on intervals.icu. The bounds only catch
    typos (a pace in minutes, a missing digit), not unusual athletes.
    """

    model_config = ConfigDict(extra="forbid")

    sport: Literal["running", "cycling", "swimming"]
    lthr_bpm: int | None = Field(default=None, ge=80, le=230)
    ftp_watts: int | None = Field(default=None, ge=50, le=1000)
    threshold_pace_sec_per_km: float | None = Field(default=None, ge=120, le=900)
    css_sec_per_100m: float | None = Field(default=None, ge=50, le=300)

    @model_validator(mode="after")
    def _fits_the_sport(self) -> ThresholdsRequest:
        """Refuse a threshold the sport does not use."""
        sent = {
            name for name in _THRESHOLD_FIELDS_ALL if getattr(self, name) is not None
        }
        stray = sorted(sent - _THRESHOLD_FIELDS[self.sport])
        if stray:
            raise ValueError(f"{self.sport} has no {', '.join(stray)}.")
        return self


class ReplanRequest(BaseModel):
    """Params of the ``replan`` RPC method.

    ``confirm`` acknowledges the read-back diff: without it a replan that
    would overwrite manually-edited intervals.icu events is refused.
    """

    confirm: bool = False


class ScheduleRequest(BaseModel):
    """Request to move a plan session to another date.

    Edits the materialized ``plan_sessions`` layer only — planner is not
    involved; the next replan supersedes manual moves.
    """

    date: str
