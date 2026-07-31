"""
NPC Manager Runtime — Room 与 NPC Manager 之间的运行时外观。

根据需求职责边界：

Director 决定 -> Room 协调 -> NPC Manager 管理 -> NPC Agent 行动

调用链：
1. Director 输出 npc_tasks 到 decision
2. Room 逐 NPC 调用此模块
3. 此模块调用 NPC Manager 构建过滤上下文
4. 调用 LLM 生成 NPC 提议
5. 校验提议
6. 记录已接受事件
"""
from __future__ import annotations

import sys, os, json, re
from dataclasses import asdict
from typing import Any
import runtime_context
if __package__:
    from . import contracts
else:
    import contracts

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if __package__:
    from . import llm
    from .npc_agent import LLMNPCRuntime, NPCAbstention
    from .npc_manager import (
        NPCManager,
        JsonNPCRepository,
        DirectorTask,
        TaskSource,
        NarrativeFunction,
        NPCProfile,
        NPCProposal,
        NPCStatus,
        AcceptedNPCEvent,
    )
else:
    import llm
    from npc_agent import LLMNPCRuntime, NPCAbstention
    from npc_manager import (
    NPCManager,
    JsonNPCRepository,
    DirectorTask,
    TaskSource,
    NarrativeFunction,
    NPCProfile,
    NPCProposal,
    NPCStatus,
    AcceptedNPCEvent,
    )

# 全局 NPC Manager 实例
_manager: NPCManager | None = None
_manager_path: str | None = None
NPC_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "contexts",
    "npc_records.json",
)


def _get_manager() -> NPCManager:
    global _manager, _manager_path
    current_path = runtime_context.scoped_path(NPC_STORE_PATH)
    if _manager is None or _manager_path != current_path:
        _manager = NPCManager(
            repository=JsonNPCRepository(current_path),
            profile_generator=None,
            runtime=LLMNPCRuntime(),
        )
        _manager_path = current_path
    return _manager


# ── 生命周期管理 ──────────────────────────────────────────


def register_profile(profile_spec: Any, *, story_id: str) -> str:
    """Register a complete Story Generator profile with NPC Manager."""
    manager = _get_manager()
    npc_id = str(profile_spec.profile_id)
    function = NarrativeFunction(str(profile_spec.narrative_function))
    profile = NPCProfile(
        npc_id=npc_id,
        profile_id=str(profile_spec.profile_id),
        status=NPCStatus.READY,
        name=str(profile_spec.name),
        public_role=str(profile_spec.public_role),
        short_background=str(profile_spec.background),
        personality=tuple(profile_spec.personality),
        current_goal=str(profile_spec.goals[0]),
        goals=tuple(profile_spec.goals),
        relation_to_yangjian=str(profile_spec.relation_to_yangjian),
        relation_to_user=str(profile_spec.relation_to_user),
        expression_style=str(profile_spec.expression_style),
        knows=tuple(profile_spec.knows),
        must_not_know=tuple(profile_spec.must_not_know),
        behavior_boundaries=tuple(profile_spec.behavior_boundaries),
        memory_seed=tuple(profile_spec.memory_seed),
        story_bindings=tuple(profile_spec.story_bindings),
        supported_functions=(function,),
        reusable=bool(profile_spec.reusable),
        profile_version=int(profile_spec.profile_version),
    )
    record = manager.register_profile(
        profile,
        story_id=story_id,
        requirement_id=str(profile_spec.requirement_id),
    )
    return record.profile.npc_id


def registry_snapshot() -> dict[str, Any]:
    records = _get_manager().repository.list_all()
    return {
        "profiles": [
            {
                "profile_id": record.profile.profile_id or record.profile.npc_id,
                "npc_id": record.profile.npc_id,
                "status": record.profile.status.value,
                "profile_version": record.profile.profile_version,
            }
            for record in records
        ]
    }


def execute_command(
    command: dict[str, Any],
    *,
    profile_spec: Any | None,
    story_id: str,
    side_arc_id: str,
) -> dict[str, Any]:
    """Execute a validated Director NPC command deterministically."""
    command_id = str(command.get("command_id", ""))
    operation = str(command.get("operation", ""))
    profile_id = str(command.get("profile_id", ""))
    try:
        if operation == contracts.NPCOperation.ENSURE_REGISTERED.value:
            if profile_spec is None:
                raise ValueError(f"Unknown StoryPlan profile: {profile_id}")
            npc_id = register_profile(profile_spec, story_id=story_id)
            record = _get_manager().repository.get(npc_id)
        elif operation == contracts.NPCOperation.ACTIVATE.value:
            npc_id = str(command.get("npc_id") or profile_id)
            if _get_manager().repository.get(npc_id) is None:
                if profile_spec is None:
                    raise ValueError(f"Unknown StoryPlan profile: {profile_id}")
                npc_id = register_profile(profile_spec, story_id=story_id)
            record = activate(
                npc_id,
                story_id=story_id,
                side_arc_id=side_arc_id,
                scene_id=str(command.get("target_scene_id") or ""),
                reason=str(command.get("reason") or "Director activated NPC"),
            )
        elif operation == contracts.NPCOperation.DEACTIVATE.value:
            npc_id = str(command.get("npc_id") or profile_id)
            record = deactivate(
                npc_id, reason=str(command.get("reason") or "Director deactivated NPC")
            )
        elif operation == contracts.NPCOperation.COMPLETE.value:
            npc_id = str(command.get("npc_id") or profile_id)
            record = complete(
                npc_id, reason=str(command.get("reason") or "Director completed NPC")
            )
        else:
            raise ValueError(f"Unsupported NPC operation: {operation}")
        return {
            "command_id": command_id,
            "status": "applied",
            "npc_id": record.profile.npc_id if record else None,
            "lifecycle_state": record.profile.status.value if record else "ready",
            "reason_code": None,
        }
    except Exception as exc:
        return {
            "command_id": command_id,
            "status": "rejected",
            "npc_id": None,
            "lifecycle_state": "unknown",
            "reason_code": str(exc),
        }


def acquire_for_side_arc(requirement: Any, side_arc_id: str) -> str | None:
    """Compatibility adapter for old callers; never invokes a runtime LLM."""
    try:
        import story_state
        profile_spec = story_state.get_npc_profile(requirement.requirement_id)
        if profile_spec is None:
            return None
        return register_profile(profile_spec, story_id=requirement.story_id)
    except Exception:
        return None


def activate(
    npc_id: str,
    *,
    story_id: str = "story_1",
    side_arc_id: str = "",
    scene_id: str = "",
    reason: str = "Director activated NPC",
):
    manager = _get_manager()
    current = manager.repository.get(npc_id)
    if current and current.profile.status is NPCStatus.ACTIVE:
        return current
    return manager.activate(
        npc_id,
        story_id=story_id,
        side_arc_id=side_arc_id,
        scene_id=scene_id,
        reason=reason,
    )


def deactivate(npc_id: str, *, reason: str = "Scene ended"):
    return _get_manager().deactivate(npc_id, reason=reason)


def complete(npc_id: str, *, reason: str = "Side arc completed"):
    return _get_manager().complete(npc_id, reason=reason)


def record_accepted(npc_id: str, *, event_id: str, summary: str):
    if not summary.strip():
        return _get_manager().repository.get(npc_id)
    return _get_manager().record_accepted_event(
        npc_id,
        AcceptedNPCEvent(event_id=event_id, summary=summary),
    )


# ── 运行时执行业务 ──────────────────────────────────────


def act_turn(turn_input: contracts.NPCTurnInput) -> dict[str, Any]:
    """Run one NPC Agent with a structured input and return ActorTurnResult."""
    task = {
        "task_id": turn_input.task.task_id,
        "objective": turn_input.task.objective,
        "source_reference": turn_input.task.source_reference,
        "known_facts": [fact.text for fact in turn_input.perception],
        "allowed_actions": list(turn_input.task.allowed_actions),
        "must_not": list(turn_input.task.constraints),
    }
    recent_events = [
        f"{message.role}: {message.text}"
        for message in turn_input.public_room_history
    ]
    result = act_for_task(turn_input.npc_id, task, recent_events)
    return dict(result["turn_result"])


def handle_agent_message(
    message: contracts.AgentMessage[contracts.NPCTurnInput],
) -> contracts.AgentMessage[contracts.ActorTurnResult]:
    """NPC Agent endpoint; NPC Manager has already built the input."""
    if message.phase is not contracts.Phase.ACT:
        raise ValueError("NPC Agent received a message outside ACT phase")
    result = contracts.actor_turn_result_from_dict(act_turn(message.payload))
    return contracts.new_message(
        turn_id=message.turn_id,
        story_id=message.story_id,
        beat_id=message.beat_id,
        phase=contracts.Phase.ACT,
        sender=message.recipient,
        recipient=message.sender,
        message_type="npc.turn.result",
        correlation_id=message.message_id,
        payload=result,
    )


def build_structured_turn_input(
    npc_id: str,
    *,
    task: contracts.AgentTask,
    scene: dict[str, Any],
    public_room_history: tuple[contracts.PublishedMessage, ...],
    perception: tuple[contracts.FactRef, ...],
) -> contracts.NPCTurnInput:
    """NPC Manager owns profile/memory access and builds the Agent input."""
    record = _get_manager().repository.get(npc_id)
    if record is None:
        return contracts.NPCTurnInput(
            task=task,
            npc_id=npc_id,
            npc_profile={},
            npc_memory={},
            scene=scene,
            public_room_history=public_room_history,
            perception=perception,
        )
    return contracts.NPCTurnInput(
        task=task,
        npc_id=npc_id,
        npc_profile=asdict(record.profile),
        npc_memory=asdict(record.memory),
        scene=scene,
        public_room_history=public_room_history,
        perception=perception,
        relationship_state={
            "yangjian": record.profile.relation_to_yangjian,
            "user": record.profile.relation_to_user,
        },
    )


def act_for_task(npc_id: str, task: dict[str, Any], recent_events: list[str]) -> dict[str, Any]:
    """
    为单个 NPC 执行一次回合，通过 NPCManager.request_proposal 调用 NPCRuntime。
    """
    mgr = _get_manager()
    record = mgr.repository.get(npc_id)
    if not record:
        return _missing_npc_result(npc_id, task)

    director_task = DirectorTask(
        task_id=str(task.get("task_id") or npc_id),
        source=TaskSource.DIRECTOR_TASK,
        source_reference=task.get("source_reference", "room_runtime"),
        objective=task.get("objective", ""),
        visible_events=tuple(task.get("visible_events", []) + recent_events),
        known_facts=tuple(task.get("known_facts", [])),
        allowed_actions=tuple(task.get("allowed_actions", [])),
        must_not=tuple(task.get("must_not", [])),
    )

    try:
        proposal, validation = mgr.request_proposal(npc_id, director_task)
    except NPCAbstention as exc:
        return _abstain_result(npc_id, director_task.task_id, exc.abstention)

    if not validation.is_valid:
        return _abstain_result(
            npc_id,
            director_task.task_id,
            {
                "request_id": f"abstain_{npc_id}_{director_task.task_id}",
                "reason_code": "PROPOSAL_VALIDATION_FAILED",
                "reason": "NPC proposal violated its profile or task boundary",
                "blocked_by": validation.issues,
                "suggested_condition": (
                    "Director should issue a narrower valid task"
                ),
            },
        )

    return _proposal_result(npc_id, director_task.task_id, proposal, task)


def _missing_npc_result(npc_id: str, task: dict[str, Any]) -> dict[str, Any]:
    abstention = contracts.AbstainRequest(
        request_id=f"abstain_{npc_id}_missing",
        task_id=str(task.get("task_id") or npc_id),
        agent_id=npc_id,
        reason_code="NPC_NOT_ACTIVE",
        reason="NPC Manager has no active instance for this profile",
        blocked_by=("npc_registration", "npc_activation"),
        suggested_condition=(
            "Register and activate the NPC through NPC Manager"
        ),
    )
    turn_result = contracts.ActorTurnResult(
        result_id=abstention.request_id,
        task_id=abstention.task_id,
        agent_id=npc_id,
        kind=contracts.ActorResultKind.ABSTAIN,
        abstention=abstention,
    )
    return {
        "actions": [],
        "dialogues": [],
        "npc_id": npc_id,
        "turn_result": contracts.to_dict(turn_result),
    }


def _abstain_result(
    npc_id: str,
    task_id: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    abstention = contracts.AbstainRequest(
        request_id=str(data.get("request_id") or f"abstain_{npc_id}_{task_id}"),
        task_id=task_id,
        agent_id=npc_id,
        reason_code=str(data.get("reason_code") or "INSUFFICIENT_CONTEXT"),
        reason=str(data.get("reason") or "NPC cannot act consistently"),
        blocked_by=tuple(data.get("blocked_by", ())),
        suggested_condition=str(data.get("suggested_condition", "")),
    )
    turn_result = contracts.ActorTurnResult(
        result_id=abstention.request_id,
        task_id=task_id,
        agent_id=npc_id,
        kind=contracts.ActorResultKind.ABSTAIN,
        abstention=abstention,
    )
    return {
        "actions": [],
        "dialogues": [],
        "npc_id": npc_id,
        "turn_result": contracts.to_dict(turn_result),
    }


def _proposal_result(
    npc_id: str,
    task_id: str,
    proposal: NPCProposal,
    task: dict[str, Any],
) -> dict[str, Any]:
    actions = [proposal.action] if proposal.action else []
    dialogues = [proposal.utterance] if proposal.utterance else []
    structured_proposal = contracts.ActorProposal(
        proposal_id=f"proposal_{npc_id}_{task_id}",
        task_id=task_id,
        agent_id=npc_id,
        intent=proposal.intent,
        dialogue=(
            contracts.DialogueProposal(text=proposal.utterance)
            if proposal.utterance
            else None
        ),
        action=(
            contracts.ActionProposal(
                description=proposal.action,
                expected_effects=tuple(proposal.proposed_effects),
            )
            if proposal.action
            else None
        ),
        proposed_effects=tuple(proposal.proposed_effects),
        referenced_fact_ids=tuple(task.get("information_ids", ())),
    )
    turn_result = contracts.ActorTurnResult(
        result_id=structured_proposal.proposal_id,
        task_id=task_id,
        agent_id=npc_id,
        kind=contracts.ActorResultKind.PROPOSAL,
        proposal=structured_proposal,
    )
    return {
        "actions": actions,
        "dialogues": dialogues,
        "npc_id": npc_id,
        "proposed_effects": list(proposal.proposed_effects),
        "turn_result": contracts.to_dict(turn_result),
    }


# ── 向后兼容的 act() 接口（Room 调用） ──────────────────


def act(npc_name: str, director_decision: dict[str, Any], perception: str = "") -> dict[str, Any]:
    """
    旧接口兼容封装。Room 的 tick() 调用此函数。
    优先使用 decision 中的 npc_tasks 结构化任务。
    """
    tasks = director_decision.get("npc_tasks", {})
    if npc_name in tasks:
        recent_events = director_decision.get("outcome", "")
        return act_for_task(npc_name, tasks[npc_name], [recent_events])

    # 无结构化任务时退回到传统模式（从 decision 获取上下文）
    event_context = director_decision.get("outcome", "无")
    scene = director_decision.get("scene", "")
    goals = director_decision.get("goals", {})

    # 构造一个最简单的 task
    task = {
        "objective": f"根据场景 {scene} 和事件 {event_context} 行动",
        "allowed_actions": ["speak", "act"],
        "must_not": ["break_character", "reveal_secrets"],
        "visible_events": [event_context],
    }
    return act_for_task(npc_name, task, [event_context])
