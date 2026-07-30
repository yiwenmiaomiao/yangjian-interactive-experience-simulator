"""Information filtering and proposal authorization rules."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    DirectorTask,
    NPCProposal,
    NPCRecord,
    NPCStatus,
    NPCTurnContext,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalValidation:
    issues: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues


def build_turn_context(record: NPCRecord, task: DirectorTask) -> NPCTurnContext:
    if record.profile.status is not NPCStatus.ACTIVE:
        raise ValueError("Only an active NPC may receive a turn")

    forbidden = set(record.profile.must_not_know)
    known = _deduplicate(
        (
            *record.profile.knows,
            *record.memory.learned_facts,
            *task.known_facts,
        )
    )
    safe_known = tuple(item for item in known if item not in forbidden)
    safe_events = tuple(
        item for item in _deduplicate(task.visible_events) if item not in forbidden
    )

    return NPCTurnContext(
        npc_id=record.profile.npc_id,
        profile=record.profile,
        memory=record.memory,
        task=task,
        visible_events=safe_events,
        known_facts=safe_known,
    )


def validate_proposal(
    record: NPCRecord,
    task: DirectorTask,
    proposal: NPCProposal,
) -> ProposalValidation:
    issues: list[str] = []

    if record.profile.status is not NPCStatus.ACTIVE:
        issues.append("npc_not_active")
    if proposal.npc_id != record.profile.npc_id:
        issues.append("npc_id_mismatch")
    if proposal.proactive and not task.allowed_actions:
        issues.append("proactive_action_not_authorized")

    combined_output = f"{proposal.utterance}\n{proposal.action}".casefold()
    forbidden = (*record.profile.must_not_know, *task.must_not)
    for item in forbidden:
        normalized = item.strip().casefold()
        if normalized and normalized in combined_output:
            issues.append(f"forbidden_content:{item}")

    return ProposalValidation(issues=tuple(issues))


def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
