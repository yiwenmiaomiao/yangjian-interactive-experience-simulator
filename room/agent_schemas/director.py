"""Director DIRECT / RESOLVE structured outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ObservedUserIntentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class UserTurnDisclosureOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    mode: Literal[
        "none",
        "environment",
        "discovery",
        "confirmation",
    ]


class UserTurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "dialogue",
        "physical_action",
        "declarative_choice",
        "passive",
        "meta",
    ]
    target: str | None = None
    disclosure: UserTurnDisclosureOutput


class ResolveGateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    reason: str = Field(min_length=1)
    act_required: bool


class StateOperationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    value: Any
    reason: str = Field(min_length=1)


class PresentationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    purpose: str
    timing: str


class UserFeedbackOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_summary: str
    revealed_fact_ids: list[str] = Field(default_factory=list)
    presentation: PresentationOutput


class InlineEffectsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state_operations: list[StateOperationOutput] = Field(default_factory=list)
    user_feedback: UserFeedbackOutput | None = None


class DirectorTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    information_ids: list[str] = Field(default_factory=list)
    success_condition: str = Field(min_length=1)


class DirectorNarrationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    purpose: str
    timing: str
    visible_facts: list[str] = Field(default_factory=list)
    max_characters: int = Field(ge=0, le=200)
    brief: str = ""
    scene_facts: list[str] = Field(default_factory=list)


class NPCCommandOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(min_length=1)
    operation: Literal[
        "ensure_registered",
        "activate",
        "deactivate",
        "complete",
    ]
    profile_id: str = Field(min_length=1)
    npc_id: str | None = None
    target_scene_id: str | None = None
    reason: str = Field(min_length=1)


class DirectorDirectiveOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["DIRECT"] = "DIRECT"
    chapter: str = Field(min_length=1)
    beat: str = Field(min_length=1)
    observed_user_intent: ObservedUserIntentOutput
    user_turn: UserTurnOutput
    resolve_gate: ResolveGateOutput
    inline_effects: InlineEffectsOutput
    tasks: list[DirectorTaskOutput] = Field(default_factory=list)
    desired_progress: Literal["maintain", "advance", "recover"]
    selected_side_arc: str | None = None
    narration: DirectorNarrationOutput
    npc_commands: list[NPCCommandOutput] = Field(default_factory=list)
    fallback_world_event: dict[str, Any] | None = None


class UserOutcomeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applies: bool
    result: Literal[
        "accepted",
        "partial",
        "failed",
        "not_applicable",
    ]
    outcome_summary: str
    revealed_fact_ids: list[str] = Field(default_factory=list)
    presentation: PresentationOutput


class ResolutionDecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    result: Literal[
        "accept",
        "modify",
        "reject",
        "accept_abstention",
    ]
    outcome_summary: str = Field(min_length=1)


class ContinuationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "continue_current",
        "redispatch",
        "world_event",
        "advance",
    ]
    reason: str = Field(min_length=1)
    target_id: str | None = None
    world_event: dict[str, Any] | None = None


class DirectorResolutionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["RESOLVE"] = "RESOLVE"
    chapter: str = Field(min_length=1)
    beat: str = Field(min_length=1)
    decisions: list[ResolutionDecisionOutput] = Field(default_factory=list)
    user_outcome: UserOutcomeOutput
    state_changes: list[StateOperationOutput] = Field(default_factory=list)
    next_beat: str | None = None
    continuation: ContinuationOutput
