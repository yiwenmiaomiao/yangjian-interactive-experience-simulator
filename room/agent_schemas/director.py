"""Director DIRECT / RESOLVE structured outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_ids import coerce_target_in_pool, normalize_agent_id

# Shared config: ignore extra fields so LLM can include redundant keys
# (mode/chapter/beat etc.) without failing validation. Room overrides them.
_CFG = ConfigDict(extra="ignore")


class ObservedUserIntentOutput(BaseModel):
    model_config = _CFG

    intent: str = Field(min_length=1)


class UserTurnDisclosureOutput(BaseModel):
    model_config = _CFG

    required: bool


class UserTurnOutput(BaseModel):
    model_config = _CFG

    kind: Literal[
        "dialogue",
        "physical_action",
        "declarative_choice",
        "passive",
        "meta",
    ]
    disclosure: UserTurnDisclosureOutput


class ResolveGateOutput(BaseModel):
    model_config = _CFG

    required: bool
    act_required: bool


class StateOperationOutput(BaseModel):
    model_config = _CFG

    key: str = Field(min_length=1)
    value: Any
    reason: str = Field(min_length=1)


class PresentationOutput(BaseModel):
    model_config = _CFG

    required: bool
    purpose: str
    timing: str


class UserFeedbackOutput(BaseModel):
    model_config = _CFG

    outcome_summary: str
    revealed_fact_ids: list[str] = Field(default_factory=list)
    presentation: PresentationOutput


class InlineEffectsOutput(BaseModel):
    model_config = _CFG

    state_operations: list[StateOperationOutput] = Field(default_factory=list)
    user_feedback: UserFeedbackOutput | None = None


class DirectorTaskOutput(BaseModel):
    model_config = _CFG

    target: str = Field(
        min_length=1,
        description="Canonical English agent_id (yangjian), never display name",
    )
    objective: str = Field(min_length=1)

    @field_validator("target", mode="before")
    @classmethod
    def _normalize_target(cls, value: Any) -> str:
        result = coerce_target_in_pool(value, allow_none=False)
        assert result is not None
        return result


class SceneUpdateOutput(BaseModel):
    """Optional scene change directive from Director.

    All fields are optional; null/missing means "no change".
    Only non-null values are merged into world_state.scene.
    """

    model_config = _CFG

    location: str | None = None
    weather: str | None = None
    time_of_day: str | None = None
    mood: str | None = None


class DirectorNarrationOutput(BaseModel):
    model_config = _CFG

    required: bool
    purpose: str
    timing: str
    narration_type: str = "旁白"
    brief: str = ""


class NPCCommandOutput(BaseModel):
    model_config = _CFG

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

    @field_validator("npc_id", mode="before")
    @classmethod
    def _normalize_npc_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        # NPC command ids may be pool members or free profile wires;
        # normalize aliases but only enforce pool when already registered.
        return normalize_agent_id(text)


class DirectorDirectiveOutput(BaseModel):
    """LLM output for DIRECT phase.

    Trimmed to only fields Room actually reads for logic decisions.
    Removed fields are auto-filled by Room:
    - mode/chapter/beat: Room overrides from beat_info
    - selected_side_arc/fallback_world_event: never used
    - source_reference/success_condition: Room sets defaults
    - npc_commands: Room auto-generates from beat NPC profiles
    - inline_effects: Room fills default; only fast path uses it
    - confidence: Room never reads it
    - user_turn.target/disclosure.mode: Room never reads them
    - resolve_gate.reason: only used in logs
    - task_id/information_ids: Room auto-fills
    - desired_progress: Room only logs it
    - narration.visible_facts/max_characters/scene_facts: Room/sanitize fills defaults
    """

    model_config = _CFG

    observed_user_intent: ObservedUserIntentOutput
    user_turn: UserTurnOutput
    resolve_gate: ResolveGateOutput
    tasks: list[DirectorTaskOutput] = Field(default_factory=list)
    narration: DirectorNarrationOutput
    scene_update: SceneUpdateOutput | None = None


class UserOutcomeOutput(BaseModel):
    model_config = _CFG

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
    model_config = _CFG

    proposal_id: str = Field(min_length=1)
    result: Literal[
        "accept",
        "modify",
        "reject",
        "accept_abstention",
    ]
    outcome_summary: str = Field(min_length=1)
    # Optional confirmed text for modify/accept. Room falls back to the
    # original actor proposal when these are omitted.
    final_dialogue: dict[str, Any] | None = None
    final_action: dict[str, Any] | None = None


class ContinuationOutput(BaseModel):
    model_config = _CFG

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
    """LLM output for RESOLVE phase.

    Removed redundant fields (mode/chapter/beat) that Room always overrides.
    """

    model_config = _CFG

    decisions: list[ResolutionDecisionOutput] = Field(default_factory=list)
    user_outcome: UserOutcomeOutput
    state_changes: list[StateOperationOutput] = Field(default_factory=list)
    next_beat: str | None = None
    continuation: ContinuationOutput
    scene_update: SceneUpdateOutput | None = None
