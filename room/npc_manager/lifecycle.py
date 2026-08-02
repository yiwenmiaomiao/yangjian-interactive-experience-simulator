"""Deterministic NPC lifecycle state machine."""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum

from .models import NPCRecord, NPCStatus


class TransitionTrigger(StrEnum):
    DIRECTOR_APPROVED = "director_approved"
    SCENE_ENDED = "scene_ended"
    SIDE_ARC_COMPLETED = "side_arc_completed"
    MAIN_ARC_ENDED = "main_arc_ended"
    REUSE_APPROVED = "reuse_approved"


class InvalidLifecycleTransition(ValueError):
    pass


_ALLOWED: dict[tuple[NPCStatus, NPCStatus], frozenset[TransitionTrigger]] = {
    (NPCStatus.READY, NPCStatus.ACTIVE): frozenset(
        {TransitionTrigger.DIRECTOR_APPROVED}
    ),
    (NPCStatus.ACTIVE, NPCStatus.INACTIVE): frozenset(
        {TransitionTrigger.SCENE_ENDED}
    ),
    (NPCStatus.INACTIVE, NPCStatus.ACTIVE): frozenset(
        {TransitionTrigger.DIRECTOR_APPROVED}
    ),
    (NPCStatus.ACTIVE, NPCStatus.COMPLETED): frozenset(
        {TransitionTrigger.SIDE_ARC_COMPLETED}
    ),
    (NPCStatus.INACTIVE, NPCStatus.COMPLETED): frozenset(
        {TransitionTrigger.SIDE_ARC_COMPLETED}
    ),
    (NPCStatus.COMPLETED, NPCStatus.ACTIVE): frozenset(
        {TransitionTrigger.REUSE_APPROVED}
    ),
    (NPCStatus.ARCHIVED, NPCStatus.READY): frozenset(
        {TransitionTrigger.REUSE_APPROVED}
    ),
}


def transition(
    record: NPCRecord,
    *,
    target: NPCStatus,
    trigger: TransitionTrigger,
    reason: str,
    story_id: str | None = None,
    arc_id: str | None = None,
    scene_id: str | None = None,
) -> NPCRecord:
    """Transition an NPC record and update its active bindings."""

    if not reason.strip():
        raise ValueError("A lifecycle transition requires a reason")

    source = record.profile.status
    if source is target:
        return record

    if target is NPCStatus.ARCHIVED:
        if trigger is not TransitionTrigger.MAIN_ARC_ENDED:
            raise InvalidLifecycleTransition(
                "NPCs may only be archived when the main arc ends"
            )
        if source is NPCStatus.ARCHIVED:
            return record
    else:
        allowed_triggers = _ALLOWED.get((source, target), frozenset())
        if trigger not in allowed_triggers:
            raise InvalidLifecycleTransition(
                f"Cannot transition {source.value} -> {target.value} "
                f"with trigger {trigger.value}"
            )

    if target is NPCStatus.ACTIVE:
        if not story_id or not arc_id or not scene_id:
            raise ValueError(
                "Activating an NPC requires story_id, arc_id and scene_id"
            )
        story_ids = (
            record.story_ids
            if story_id in record.story_ids
            else (*record.story_ids, story_id)
        )
        return replace(
            record,
            profile=replace(record.profile, status=target),
            story_ids=story_ids,
            active_story_id=story_id,
            active_arc_id=arc_id,
            active_scene_id=scene_id,
            last_transition_reason=reason,
        )

    if target is NPCStatus.INACTIVE:
        return replace(
            record,
            profile=replace(record.profile, status=target),
            active_scene_id=None,
            last_transition_reason=reason,
        )

    if target in {NPCStatus.COMPLETED, NPCStatus.ARCHIVED, NPCStatus.READY}:
        return replace(
            record,
            profile=replace(record.profile, status=target),
            active_story_id=None,
            active_arc_id=None,
            active_scene_id=None,
            last_transition_reason=reason,
        )

    raise InvalidLifecycleTransition(f"Unhandled target state: {target}")
