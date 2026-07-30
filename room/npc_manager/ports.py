"""Project-specific integrations intentionally left as protocols."""

from __future__ import annotations

from typing import Protocol

from .models import (
    DirectorTask,
    NPCProfile,
    NPCProposal,
    NPCRecord,
    NPCTurnContext,
)
from yangjian_story_generator.models import NPCRequirement


class NPCRepository(Protocol):
    def get(self, npc_id: str) -> NPCRecord | None: ...

    def list_all(self) -> tuple[NPCRecord, ...]: ...

    def save(self, record: NPCRecord) -> None:
        """Persist an NPC record.

        TODO(project integration): implement with the project's real database
        or private state store, including migrations and atomic updates.
        """


class NPCProfileGenerator(Protocol):
    def generate(self, requirement: NPCRequirement) -> NPCProfile:
        """Generate a minimal NPC profile.

        TODO(model integration): implement using the project's model SDK with
        structured output, timeout, retry, token accounting and redacted logs.
        """


class NPCRuntime(Protocol):
    def run_turn(self, context: NPCTurnContext) -> NPCProposal:
        """Run one logical NPC turn and return a proposal.

        TODO(Hermes integration): launch a fresh Hermes leaf child session for
        this turn, supplying the NPC profile/memory through the context adapter.
        NPC identity persists in NPCRepository, not in a long-lived process.
        """


class AsyncNPCRuntime(Protocol):
    async def run_turn(self, context: NPCTurnContext) -> NPCProposal:
        """Async NPC turn for Hermes gateway/plugin execution.

        TODO(Hermes integration): use ``ctx.subagent_lifecycle`` from an active
        Hermes turn. Handles are operational only and must not be the durable
        NPC identity.
        """


class SemanticReuseReviewer(Protocol):
    def is_compatible(
        self,
        *,
        requirement: NPCRequirement,
        candidate: NPCRecord,
    ) -> bool:
        """Resolve an ambiguous reuse decision.

        TODO(model integration): call only after deterministic filtering leaves
        an ambiguous candidate. Do not call for obvious matches or conflicts.
        """
