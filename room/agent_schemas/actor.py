"""Actor (Yang Jian) structured turn output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DialogueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    intent: str = ""
    addressee_ids: list[str] = Field(default_factory=list)


class ActionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    action_type: str = "act"
    target_ids: list[str] = Field(default_factory=list)
    expected_effects: list[str] = Field(default_factory=list)


class ActorProposalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1)
    dialogue: DialogueOutput | None = None
    action: ActionOutput | None = None
    proposed_effects: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    referenced_fact_ids: list[str] = Field(default_factory=list)


class ActorAbstentionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    blocked_by: list[str] = Field(default_factory=list)
    suggested_condition: str = ""


class RelationshipFeedback(BaseModel):
    """杨戬对用户的内心感受（仅在 checkpoint beat 输出）。

    Yangjian decides based on his own feelings and persona.
    Changes can be empty (no shift this turn).
    """
    model_config = ConfigDict(extra="ignore")

    changes: dict[str, int] = Field(default_factory=dict)
    reason: str = ""


class ActorTurnOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    result_type: Literal["proposal", "abstain"]
    proposal: ActorProposalOutput | None = None
    abstention: ActorAbstentionOutput | None = None
    # Optional: only output when current beat has a relationship_checkpoint
    relationship_feedback: RelationshipFeedback | None = None
