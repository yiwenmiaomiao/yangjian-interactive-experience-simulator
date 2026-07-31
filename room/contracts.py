"""Versioned contracts shared by Room and every runtime agent.

The envelope owns routing and correlation metadata.  Payload dataclasses own
agent-specific business data.  Deterministic services such as NPC Manager use
the same value objects, but are not represented as acting agents.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, Mapping, TypeVar
from uuid import uuid4


SCHEMA_VERSION = "1.0"


def _text(value: str, field_name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{field_name} must not be blank")
    return result


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Phase(StrEnum):
    PLAN = "PLAN"
    DIRECT = "DIRECT"
    ACT = "ACT"
    RESOLVE = "RESOLVE"
    NARRATE = "NARRATE"
    PUBLISH = "PUBLISH"


class AgentKind(StrEnum):
    ROOM = "room"
    DIRECTOR = "director"
    ACTOR = "actor"
    NARRATOR = "narrator"
    STORY_GENERATOR = "story_generator"
    SYSTEM_SERVICE = "system_service"


class ActorResultKind(StrEnum):
    PROPOSAL = "proposal"
    ABSTAIN = "abstain"


class NPCOperation(StrEnum):
    ENSURE_REGISTERED = "ensure_registered"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentRef:
    agent_id: str
    kind: AgentKind
    instance_id: str = ""
    profile_version: str = ""

    def __post_init__(self) -> None:
        _text(self.agent_id, "agent_id")


@dataclass(frozen=True, slots=True, kw_only=True)
class FactRef:
    fact_id: str
    text: str
    visibility: str = "public"
    source_event_id: str = ""
    version: int = 1

    def __post_init__(self) -> None:
        _text(self.fact_id, "fact_id")
        _text(self.text, "fact text")


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishedMessage:
    message_id: str
    role: str
    kind: str
    text: str
    turn_id: str = ""
    confirmed_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTask:
    task_id: str
    target_agent_id: str
    objective: str
    source_reference: str
    visible_facts: tuple[FactRef, ...] = ()
    allowed_actions: tuple[str, ...] = ("speak", "act")
    constraints: tuple[str, ...] = ()
    success_condition: str = "Produce a character-consistent turn result"

    def __post_init__(self) -> None:
        _text(self.task_id, "task_id")
        _text(self.target_agent_id, "target_agent_id")
        _text(self.objective, "objective")
        _text(self.source_reference, "source_reference")


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCCommand:
    command_id: str
    operation: NPCOperation
    profile_id: str
    npc_id: str | None = None
    target_scene_id: str | None = None
    reason: str

    def __post_init__(self) -> None:
        _text(self.command_id, "command_id")
        _text(self.profile_id, "profile_id")
        _text(self.reason, "reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class NarrationRequest:
    purpose: str
    timing: str = "after_dialogue"
    visible_fact_ids: tuple[str, ...] = ()
    max_characters: int = 100
    style_profile: str = "concise"

    def __post_init__(self) -> None:
        if not 0 < self.max_characters <= 200:
            raise ValueError("max_characters must be between 1 and 200")


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectorDirectInput:
    user_event: Mapping[str, Any]
    story_cursor: Mapping[str, Any]
    world_snapshot: Mapping[str, Any]
    available_actor_agents: tuple[AgentRef, ...]
    npc_requirements: tuple[Mapping[str, Any], ...] = ()
    npc_registry: Mapping[str, Any] = field(default_factory=dict)
    unlocked_transitions: tuple[Mapping[str, Any], ...] = ()
    available_side_arcs: tuple[Mapping[str, Any], ...] = ()
    recent_confirmed_events: tuple[Mapping[str, Any], ...] = ()
    liveness: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectorDirective:
    directive_id: str
    observed_user_intent: Mapping[str, Any]
    actor_tasks: tuple[AgentTask, ...]
    npc_commands: tuple[NPCCommand, ...] = ()
    desired_progress: str = "maintain"
    selected_side_arc_id: str | None = None
    narration_request: NarrationRequest | None = None
    fallback_world_event: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _text(self.directive_id, "directive_id")
        if not self.actor_tasks and not self.npc_commands and not self.fallback_world_event:
            raise ValueError("DirectorDirective must contain an executable next step")


@dataclass(frozen=True, slots=True, kw_only=True)
class DialogueProposal:
    text: str
    intent: str = ""
    addressee_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.text, "dialogue text")


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionProposal:
    description: str
    action_type: str = "act"
    target_ids: tuple[str, ...] = ()
    expected_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.description, "action description")


@dataclass(frozen=True, slots=True, kw_only=True)
class ActorProposal:
    proposal_id: str
    task_id: str
    agent_id: str
    intent: str
    dialogue: DialogueProposal | None = None
    action: ActionProposal | None = None
    proposed_effects: tuple[str, ...] = ()
    confidence: float = 0.5
    referenced_fact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.proposal_id, "proposal_id")
        _text(self.task_id, "task_id")
        _text(self.agent_id, "agent_id")
        _text(self.intent, "intent")
        if self.dialogue is None and self.action is None:
            raise ValueError("ActorProposal needs dialogue or action")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True, kw_only=True)
class AbstainRequest:
    request_id: str
    task_id: str
    agent_id: str
    reason_code: str
    reason: str
    blocked_by: tuple[str, ...] = ()
    suggested_condition: str = ""

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id")
        _text(self.reason_code, "reason_code")
        _text(self.reason, "reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class ActorTurnResult:
    result_id: str
    task_id: str
    agent_id: str
    kind: ActorResultKind
    proposal: ActorProposal | None = None
    abstention: AbstainRequest | None = None

    def __post_init__(self) -> None:
        if (self.proposal is None) == (self.abstention is None):
            raise ValueError("ActorTurnResult requires exactly one result payload")
        if self.kind is ActorResultKind.PROPOSAL and self.proposal is None:
            raise ValueError("proposal kind requires proposal")
        if self.kind is ActorResultKind.ABSTAIN and self.abstention is None:
            raise ValueError("abstain kind requires abstention")


@dataclass(frozen=True, slots=True, kw_only=True)
class YangJianTurnInput:
    task: AgentTask
    scene: Mapping[str, Any]
    public_room_history: tuple[PublishedMessage, ...]
    perception: tuple[FactRef, ...] = ()
    recent_memory: tuple[str, ...] = ()
    relationship_state: Mapping[str, Any] = field(default_factory=dict)
    current_stance: str | None = None
    character_id: str = "yangjian"
    soul_version: str = "current"


@dataclass(frozen=True, slots=True, kw_only=True)
class NPCTurnInput:
    task: AgentTask
    npc_id: str
    npc_profile: Mapping[str, Any]
    npc_memory: Mapping[str, Any]
    scene: Mapping[str, Any]
    public_room_history: tuple[PublishedMessage, ...]
    perception: tuple[FactRef, ...] = ()
    relationship_state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class ActorResultDecision:
    result_id: str
    result: str
    outcome_summary: str
    final_dialogue: DialogueProposal | None = None
    final_action: ActionProposal | None = None
    confirmed_events: tuple[Mapping[str, Any], ...] = ()
    reason_code: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuationPlan:
    kind: str
    reason: str
    target_id: str | None = None
    world_event: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"continue_current", "redispatch", "world_event", "advance"}:
            raise ValueError(f"Unsupported continuation kind: {self.kind}")
        _text(self.reason, "continuation reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectorResolveInput:
    directive_id: str
    story_cursor: Mapping[str, Any]
    world_snapshot: Mapping[str, Any]
    actor_results: tuple[ActorTurnResult, ...]
    unlocked_transitions: tuple[Mapping[str, Any], ...] = ()
    allowed_state_operations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectorResolution:
    resolution_id: str
    decisions: tuple[ActorResultDecision, ...]
    continuation: ContinuationPlan
    state_operations: tuple[Mapping[str, Any], ...] = ()
    next_beat_id: str | None = None
    progress_result: str = "maintained"


@dataclass(frozen=True, slots=True, kw_only=True)
class NarratorInput:
    narration_request: NarrationRequest
    scene: Mapping[str, Any]
    confirmed_events: tuple[Mapping[str, Any], ...]
    visible_facts: tuple[FactRef, ...]
    previous_published_messages: tuple[PublishedMessage, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class NarrationDraft:
    narration_id: str
    text: str
    referenced_event_ids: tuple[str, ...] = ()
    referenced_fact_ids: tuple[str, ...] = ()
    contains_dialogue: bool = False

    @property
    def character_count(self) -> int:
        return len(self.text)


PayloadT = TypeVar("PayloadT")


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentMessage(Generic[PayloadT]):
    message_id: str
    turn_id: str
    story_id: str
    beat_id: str
    phase: Phase
    sender: AgentRef
    recipient: AgentRef
    message_type: str
    payload: PayloadT
    correlation_id: str | None = None
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema version: {self.schema_version}")
        _text(self.message_id, "message_id")
        _text(self.turn_id, "turn_id")
        _text(self.story_id, "story_id")
        _text(self.beat_id, "beat_id")
        _text(self.message_type, "message_type")


def new_message(
    *,
    turn_id: str,
    story_id: str,
    beat_id: str,
    phase: Phase,
    sender: AgentRef,
    recipient: AgentRef,
    message_type: str,
    payload: PayloadT,
    correlation_id: str | None = None,
) -> AgentMessage[PayloadT]:
    return AgentMessage(
        message_id=_identifier("msg"),
        turn_id=turn_id,
        story_id=story_id,
        beat_id=beat_id,
        phase=phase,
        sender=sender,
        recipient=recipient,
        message_type=message_type,
        correlation_id=correlation_id,
        payload=payload,
    )


def to_dict(value: Any) -> Any:
    """Convert a contract object into JSON-safe primitives."""
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: to_dict(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_dict(item) for item in value]
    return value


def published_history(
    state: Mapping[str, Any],
    *,
    limit: int = 40,
) -> tuple[PublishedMessage, ...]:
    """Read structured public history, migrating legacy event-log strings."""
    structured = state.get("public_room_history", ())
    if isinstance(structured, list) and structured:
        return tuple(
            PublishedMessage(
                message_id=str(item.get("message_id", _identifier("published"))),
                turn_id=str(item.get("turn_id", "")),
                role=str(item.get("role", "")),
                kind=str(item.get("kind", "dialogue")),
                text=str(item.get("text", "")),
                confirmed_event_ids=tuple(item.get("confirmed_event_ids", ())),
            )
            for item in structured[-limit:]
            if isinstance(item, Mapping) and str(item.get("text", "")).strip()
        )
    result: list[PublishedMessage] = []
    for index, raw in enumerate(state.get("event_log", ())[-limit:]):
        text = str(raw)
        role, separator, content = text.partition(":")
        result.append(
            PublishedMessage(
                message_id=f"legacy_{index}",
                role=role.strip() if separator else "Room",
                kind="event",
                text=content.strip() if separator else text,
            )
        )
    return tuple(result)


def append_published_message(
    state: dict[str, Any],
    *,
    turn_id: str,
    role: str,
    kind: str,
    text: str,
    confirmed_event_ids: tuple[str, ...] = (),
) -> PublishedMessage:
    message = PublishedMessage(
        message_id=_identifier("published"),
        turn_id=turn_id,
        role=role,
        kind=kind,
        text=text,
        confirmed_event_ids=confirmed_event_ids,
    )
    history = state.setdefault("public_room_history", [])
    history.append(to_dict(message))
    if len(history) > 500:
        del history[:-500]
    return message


def agent_task_from_dict(data: Mapping[str, Any]) -> AgentTask:
    facts = tuple(
        FactRef(
            fact_id=str(item.get("fact_id", item)),
            text=str(item.get("text", item)),
            visibility=str(item.get("visibility", "public")),
        )
        if isinstance(item, Mapping)
        else FactRef(fact_id=str(item), text=str(item))
        for item in data.get(
            "visible_facts", data.get("information_ids", ())
        )
    )
    return AgentTask(
        task_id=str(data["task_id"]),
        target_agent_id=str(data["target_agent_id"]),
        objective=str(data["objective"]),
        source_reference=str(data["source_reference"]),
        visible_facts=facts,
        allowed_actions=tuple(data.get("allowed_actions", ("speak", "act"))),
        constraints=tuple(data.get("constraints", ())),
        success_condition=str(
            data.get(
                "success_condition",
                "Produce a character-consistent turn result",
            )
        ),
    )


def director_directive_from_dict(
    data: Mapping[str, Any],
) -> DirectorDirective:
    narration_data = data.get("narration_request")
    narration_request = (
        NarrationRequest(
            purpose=str(narration_data.get("purpose", "visible_action")),
            timing=str(narration_data.get("timing", "after_dialogue")),
            visible_fact_ids=tuple(
                narration_data.get("visible_fact_ids", ())
            ),
            max_characters=int(narration_data.get("max_characters", 100)),
            style_profile=str(
                narration_data.get("style_profile", "concise")
            ),
        )
        if isinstance(narration_data, Mapping)
        else None
    )
    return DirectorDirective(
        directive_id=str(data["directive_id"]),
        observed_user_intent=dict(data.get("observed_user_intent", {})),
        actor_tasks=tuple(
            agent_task_from_dict(item)
            for item in data.get("actor_tasks", ())
            if isinstance(item, Mapping)
        ),
        npc_commands=tuple(
            NPCCommand(
                command_id=str(item["command_id"]),
                operation=NPCOperation(str(item["operation"])),
                profile_id=str(item["profile_id"]),
                npc_id=(
                    str(item["npc_id"]) if item.get("npc_id") else None
                ),
                target_scene_id=(
                    str(item["target_scene_id"])
                    if item.get("target_scene_id")
                    else None
                ),
                reason=str(item["reason"]),
            )
            for item in data.get("npc_commands", ())
            if isinstance(item, Mapping)
        ),
        desired_progress=str(data.get("desired_progress", "maintain")),
        selected_side_arc_id=data.get("selected_side_arc_id"),
        narration_request=narration_request,
        fallback_world_event=(
            dict(data["fallback_world_event"])
            if isinstance(data.get("fallback_world_event"), Mapping)
            else None
        ),
    )


def actor_turn_result_from_dict(
    data: Mapping[str, Any],
) -> ActorTurnResult:
    proposal_data = data.get("proposal")
    abstention_data = data.get("abstention")
    proposal = None
    abstention = None
    if isinstance(proposal_data, Mapping):
        dialogue_data = proposal_data.get("dialogue")
        action_data = proposal_data.get("action")
        proposal = ActorProposal(
            proposal_id=str(proposal_data["proposal_id"]),
            task_id=str(data["task_id"]),
            agent_id=str(data["agent_id"]),
            intent=str(proposal_data["intent"]),
            dialogue=(
                DialogueProposal(
                    text=str(dialogue_data["text"]),
                    intent=str(dialogue_data.get("intent", "")),
                    addressee_ids=tuple(
                        dialogue_data.get("addressee_ids", ())
                    ),
                )
                if isinstance(dialogue_data, Mapping)
                and dialogue_data.get("text")
                else None
            ),
            action=(
                ActionProposal(
                    description=str(action_data["description"]),
                    action_type=str(action_data.get("action_type", "act")),
                    target_ids=tuple(action_data.get("target_ids", ())),
                    expected_effects=tuple(
                        action_data.get("expected_effects", ())
                    ),
                )
                if isinstance(action_data, Mapping)
                and action_data.get("description")
                else None
            ),
            proposed_effects=tuple(
                proposal_data.get("proposed_effects", ())
            ),
            confidence=float(proposal_data.get("confidence", 0.5)),
            referenced_fact_ids=tuple(
                proposal_data.get("referenced_fact_ids", ())
            ),
        )
    if isinstance(abstention_data, Mapping):
        abstention = AbstainRequest(
            request_id=str(
                abstention_data.get("request_id", data["result_id"])
            ),
            task_id=str(data["task_id"]),
            agent_id=str(data["agent_id"]),
            reason_code=str(abstention_data["reason_code"]),
            reason=str(abstention_data["reason"]),
            blocked_by=tuple(abstention_data.get("blocked_by", ())),
            suggested_condition=str(
                abstention_data.get("suggested_condition", "")
            ),
        )
    return ActorTurnResult(
        result_id=str(data["result_id"]),
        task_id=str(data["task_id"]),
        agent_id=str(data["agent_id"]),
        kind=ActorResultKind(str(data["kind"])),
        proposal=proposal,
        abstention=abstention,
    )


def director_resolution_from_dict(
    data: Mapping[str, Any],
) -> DirectorResolution:
    decisions = []
    for item in data.get("decisions", ()):
        if not isinstance(item, Mapping):
            continue
        dialogue_data = item.get("final_dialogue")
        action_data = item.get("final_action")
        decisions.append(
            ActorResultDecision(
                result_id=str(
                    item.get("result_id", item.get("proposal_id", ""))
                ),
                result=str(item["result"]),
                outcome_summary=str(item.get("outcome_summary", "")),
                final_dialogue=(
                    DialogueProposal(
                        text=str(dialogue_data["text"]),
                        intent=str(dialogue_data.get("intent", "")),
                        addressee_ids=tuple(
                            dialogue_data.get("addressee_ids", ())
                        ),
                    )
                    if isinstance(dialogue_data, Mapping)
                    and dialogue_data.get("text")
                    else None
                ),
                final_action=(
                    ActionProposal(
                        description=str(action_data["description"]),
                        action_type=str(action_data.get("action_type", "act")),
                        target_ids=tuple(action_data.get("target_ids", ())),
                        expected_effects=tuple(
                            action_data.get("expected_effects", ())
                        ),
                    )
                    if isinstance(action_data, Mapping)
                    and action_data.get("description")
                    else None
                ),
                confirmed_events=tuple(item.get("confirmed_events", ())),
                reason_code=str(item.get("reason_code", "")),
            )
        )
    continuation_data = data["continuation"]
    return DirectorResolution(
        resolution_id=str(data.get("resolution_id", _identifier("resolution"))),
        decisions=tuple(decisions),
        state_operations=tuple(
            data.get("state_operations", data.get("state_changes", ()))
        ),
        next_beat_id=data.get("next_beat_id", data.get("next_beat")),
        progress_result=str(data.get("progress_result", "maintained")),
        continuation=ContinuationPlan(
            kind=str(continuation_data["kind"]),
            reason=str(continuation_data["reason"]),
            target_id=continuation_data.get("target_id"),
            world_event=(
                dict(continuation_data["world_event"])
                if isinstance(
                    continuation_data.get("world_event"), Mapping
                )
                else None
            ),
        ),
    )
