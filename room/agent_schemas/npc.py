"""NPC structured turn output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NPCProposalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = ""
    intent: str = Field(min_length=1)
    utterance: str = ""
    action: str = ""
    proposed_effects: list[str] = Field(default_factory=list)
    proactive: bool = False


class NPCAbstentionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    blocked_by: list[str] = Field(default_factory=list)
    suggested_condition: str = ""


class NPCTurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_type: Literal["proposal", "abstain"]
    proposal: NPCProposalOutput | None = None
    abstention: NPCAbstentionOutput | None = None
