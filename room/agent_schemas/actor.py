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


class ActorTurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_type: Literal["proposal", "abstain"]
    proposal: ActorProposalOutput | None = None
    abstention: ActorAbstentionOutput | None = None
