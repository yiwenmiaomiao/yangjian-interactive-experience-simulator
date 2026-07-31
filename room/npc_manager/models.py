"""Dependency-free NPC domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


class NPCStatus(StrEnum):
    READY = "ready"
    ACTIVE = "active"
    INACTIVE = "inactive"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class NarrativeFunction(StrEnum):
    CATALYST = "catalyst"
    OBSTACLE = "obstacle"
    ALLY = "ally"
    MIRROR = "mirror"
    CLUE_SOURCE = "clue_source"
    TEMPORARY_COMPANION = "temporary_companion"
    ANTAGONIST = "antagonist"


class TaskSource(StrEnum):
    STORY_BEAT = "story_beat"
    NPC_REQUIREMENT = "npc_requirement"
    DIRECTOR_TASK = "director_task"


@dataclass(frozen=True, slots=True, kw_only=True)

class NPCProfile:
    npc_id: str
    status: NPCStatus
    name: str
    public_role: str
    short_background: str
    current_goal: str
    relation_to_yangjian: str
    relation_to_user: str
    expression_style: str
    profile_id: str = ""
    personality: tuple[str, ...] = ()
    goals: tuple[str, ...] = ()
    behavior_boundaries: tuple[str, ...] = ()
    memory_seed: tuple[str, ...] = ()
    story_bindings: tuple[str, ...] = ()
    knows: tuple[str, ...] = ()
    must_not_know: tuple[str, ...] = ()
    supported_functions: tuple[NarrativeFunction, ...] = ()
    reusable: bool = True
    permanently_unavailable: bool = False
    entry_condition: str = ""
    exit_condition: str = ""
    source_requirement_ids: tuple[str, ...] = ()
    profile_version: int = 1

    def __post_init__(self) -> None:
        _require_text(self.npc_id, "npc_id")
        _require_text(self.name, "name")
        _require_text(self.public_role, "public_role")
        if self.profile_version < 1:
            raise ValueError("profile_version must be at least 1")
        overlap = set(self.knows) & set(self.must_not_know)
        if overlap:
            raise ValueError(
                f"NPC both knows and must not know: {sorted(overlap)}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCMemory:
    important_events: tuple[str, ...] = ()
    relation_to_yangjian: tuple[str, ...] = ()
    relation_to_user: tuple[str, ...] = ()
    learned_facts: tuple[str, ...] = ()
    unresolved_matters: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCRecord:
    profile: NPCProfile
    memory: NPCMemory = NPCMemory()
    story_ids: tuple[str, ...] = ()
    active_story_id: str | None = None
    active_side_arc_id: str | None = None
    active_scene_id: str | None = None
    last_transition_reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class AcceptedNPCEvent:
    event_id: str
    summary: str
    learned_facts: tuple[str, ...] = ()
    relation_to_yangjian_update: str | None = None
    relation_to_user_update: str | None = None
    unresolved_matter: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.event_id, "event_id")
        _require_text(self.summary, "summary")


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectorTask:
    task_id: str
    source: TaskSource
    source_reference: str
    objective: str
    visible_events: tuple[str, ...] = ()
    known_facts: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.task_id, "task_id")
        _require_text(self.source_reference, "source_reference")
        _require_text(self.objective, "objective")


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCTurnContext:
    npc_id: str
    profile: NPCProfile
    memory: NPCMemory
    task: DirectorTask
    visible_events: tuple[str, ...]
    known_facts: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCProposal:
    npc_id: str
    intent: str
    utterance: str = ""
    action: str = ""
    proposed_effects: tuple[str, ...] = ()
    proactive: bool = False

    def __post_init__(self) -> None:
        _require_text(self.npc_id, "npc_id")
        _require_text(self.intent, "intent")
        if not self.utterance.strip() and not self.action.strip():
            raise ValueError("A proposal needs an utterance or action")


@dataclass(slots=True)
class ManagerMetrics:
    reuse_count: int = 0
    generated_count: int = 0
    lifecycle_transitions: int = 0
    runtime_turns: int = 0
