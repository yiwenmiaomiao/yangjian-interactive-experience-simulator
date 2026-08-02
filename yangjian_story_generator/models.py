"""Dependency-free domain models for a private branching story plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeAlias

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_ratio(value: float, field_name: str) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be between 0.0 and 1.0")


class ArcKind(StrEnum):
    MAIN = "main"
    SIDE = "side"
    RECOVERY = "recovery"


class NarrativeFunction(StrEnum):
    CATALYST = "catalyst"
    OBSTACLE = "obstacle"
    ALLY = "ally"
    MIRROR = "mirror"
    CLUE_SOURCE = "clue_source"
    TEMPORARY_COMPANION = "temporary_companion"
    ANTAGONIST = "antagonist"


class ConditionOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS_THAN = "less_than"
    LESS_OR_EQUAL = "less_or_equal"
    CONTAINS = "contains"
    EXISTS = "exists"


@dataclass(frozen=True, slots=True, kw_only=True)
class CanonicalEvent:
    event_id: str
    summary: str
    source_reference: str
    lasting_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.event_id, "event_id")
        _require_text(self.summary, "summary")
        _require_text(self.source_reference, "source_reference")


@dataclass(frozen=True, slots=True, kw_only=True)
class CharacterContext:
    """Interpretation of canonical Yang Jian stories, not runtime AI behavior."""

    character_id: str
    source_version: str
    canonical_history: tuple[CanonicalEvent, ...]
    formative_experiences: tuple[str, ...] = ()
    relationship_history: tuple[str, ...] = ()
    recurring_conflicts: tuple[str, ...] = ()
    worldview: tuple[str, ...] = ()
    emotional_logic: tuple[str, ...] = ()
    narrative_constraints: tuple[str, ...] = ()
    open_interpretations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.character_id, "character_id")
        _require_text(self.source_version, "source_version")
        if not self.canonical_history:
            raise ValueError("canonical_history must contain at least one event")


@dataclass(frozen=True, slots=True, kw_only=True)
class PreferenceMeasure:
    value: JSONValue
    confidence: float
    updated_at: str
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_ratio(self.confidence, "confidence")
        _require_text(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class PreferenceSnapshot:
    """Versioned preference input exported by Hermes."""

    user_id: str
    profile_version: int
    created_at: str
    measures: dict[str, PreferenceMeasure] = field(default_factory=dict)
    hard_avoid: tuple[str, ...] = ()
    soft_avoid: tuple[str, ...] = ()
    contextual_overrides: dict[str, dict[str, JSONValue]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        _require_text(self.user_id, "user_id")
        _require_text(self.created_at, "created_at")
        if self.profile_version < 1:
            raise ValueError("profile_version must be at least 1")


@dataclass(frozen=True, slots=True, kw_only=True)
class Condition:
    key: str
    operator: ConditionOperator
    value: JSONValue = None

    def __post_init__(self) -> None:
        _require_text(self.key, "condition key")


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationshipCheckpoint:
    """Marks a beat as a relationship evaluation point.

    The description tells yangjian what this checkpoint is about
    (not what to score or how). Yangjian decides based on his own feelings.
    """
    description: str
    evaluator: str = "yangjian"

    def __post_init__(self) -> None:
        _require_text(self.description, "checkpoint description")


@dataclass(frozen=True, slots=True, kw_only=True)
class BranchTransition:
    transition_id: str
    target_id: str
    goal: str = ""
    preserved_consequences: tuple[str, ...] = ()
    # Optional relationship requirements for this transition.
    # e.g. {"trust": {"min": 2}, "closeness": {"min": 1}}
    relationship_requirements: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_text(self.transition_id, "transition_id")
        _require_text(self.target_id, "target_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class Ending:
    ending_id: str
    summary: str

    def __post_init__(self) -> None:
        _require_text(self.ending_id, "ending_id")
        _require_text(self.summary, "ending summary")


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCRequirement:
    requirement_id: str
    story_id: str
    arc_id: str
    narrative_function: NarrativeFunction
    purpose: str
    npc_background: str
    relation_to_yangjian: str
    relation_to_user: str
    current_goal: str
    must_know: tuple[str, ...] = ()
    must_not_know: tuple[str, ...] = ()
    reusable: bool = True
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.requirement_id, "requirement_id")
        _require_text(self.story_id, "story_id")
        _require_text(self.arc_id, "arc_id")
        _require_text(self.purpose, "NPC purpose")
        overlap = set(self.must_know) & set(self.must_not_know)
        if overlap:
            raise ValueError(
                f"NPC knowledge is both required and forbidden: {sorted(overlap)}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCProfileSpec:
    """Complete NPC profile generated with the private StoryPlan.

    NPC Manager registers this profile; it must not invent personality or
    knowledge at runtime.
    """

    profile_id: str
    requirement_id: str
    narrative_function: NarrativeFunction
    name: str
    public_role: str
    personality: tuple[str, ...]
    background: str
    expression_style: str
    goals: tuple[str, ...]
    relation_to_yangjian: str
    relation_to_user: str
    knows: tuple[str, ...] = ()
    must_not_know: tuple[str, ...] = ()
    behavior_boundaries: tuple[str, ...] = ()
    memory_seed: tuple[str, ...] = ()
    story_bindings: tuple[str, ...] = ()
    reusable: bool = True
    profile_version: int = 1

    def __post_init__(self) -> None:
        _require_text(self.profile_id, "profile_id")
        _require_text(self.requirement_id, "requirement_id")
        _require_text(self.name, "NPC name")
        _require_text(self.public_role, "NPC public role")
        _require_text(self.background, "NPC background")
        _require_text(self.expression_style, "NPC expression_style")
        if not self.personality:
            raise ValueError("NPC personality must not be empty")
        if not self.goals:
            raise ValueError("NPC goals must not be empty")
        if self.profile_version < 1:
            raise ValueError("profile_version must be at least 1")
        overlap = set(self.knows) & set(self.must_not_know)
        if overlap:
            raise ValueError(
                f"NPC profile both knows and must not know: {sorted(overlap)}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryBeat:
    beat_id: str
    plot: str
    participants: tuple[str, ...]
    transitions: tuple[BranchTransition, ...] = ()
    allowed_information: tuple[str, ...] = ()
    forbidden_information: tuple[str, ...] = ()
    npc_requirement_ids: tuple[str, ...] = ()
    diversion_allowed: bool = False
    # 场景状态：Room 每轮从 story plan beat 读取并更新 world_state
    world_day: str = ""
    time_of_day: str = ""
    weather: str = ""
    location: str = ""
    mood: str = ""
    # Optional: marks this beat as a relationship evaluation point.
    # When present, yangjian outputs relationship_feedback after his turn.
    relationship_checkpoint: RelationshipCheckpoint | None = None

    def __post_init__(self) -> None:
        _require_text(self.beat_id, "beat_id")
        _require_text(self.plot, "beat plot")
        if not self.participants:
            raise ValueError("participants must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class MainArc:
    goal: str
    beats: tuple[StoryBeat, ...]
    endings: tuple[Ending, ...]

    def __post_init__(self) -> None:
        _require_text(self.goal, "main goal")
        if not self.beats:
            raise ValueError("main arc must have at least one beat")


@dataclass(frozen=True, slots=True, kw_only=True)
class SideArc:
    arc_id: str
    purpose: str
    impact_on_main_arc: tuple[str, ...]
    beats: tuple[StoryBeat, ...]
    npc_requirements: tuple[NPCRequirement, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.arc_id, "side arc_id")
        _require_text(self.purpose, "side purpose")


@dataclass(frozen=True, slots=True, kw_only=True)
class Secret:
    secret_id: str
    description: str
    known_by: tuple[str, ...] = ()
    reveal_conditions: tuple[Condition, ...] = ()
    never_reveal_to: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.secret_id, "secret_id")
        _require_text(self.description, "secret description")


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryPlan:
    story_id: str
    created_at: str
    premise: str
    theme: str
    main_arc: MainArc
    side_arcs: tuple[SideArc, ...] = ()
    secrets: tuple[Secret, ...] = ()
    npc_profiles: tuple[NPCProfileSpec, ...] = ()
    global_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.story_id, "story_id")
        _require_text(self.created_at, "created_at")
        _require_text(self.premise, "premise")
        _require_text(self.theme, "theme")


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryStandard:
    version: int = 1
    required_main_participants: tuple[str, ...] = ("user", "yangjian")
    required_side_participants: tuple[str, ...] = ("user", "yangjian")
    maximum_main_endings: int = 2
    allow_graph_cycles: bool = False
    npc_only_in_side_arcs: bool = True

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("standard version must be at least 1")
        if not 1 <= self.maximum_main_endings <= 2:
            raise ValueError("maximum_main_endings must be 1 or 2")
