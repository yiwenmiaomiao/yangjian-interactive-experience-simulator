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
from typing import Any
import runtime_context

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if __package__:
    from . import llm
    from .npc_manager import (
        NPCManager,
        JsonNPCRepository,
        build_turn_context,
        validate_proposal,
        NPC_BASE_SYSTEM_PROMPT,
        build_npc_turn_input_json,
        NPC_PROPOSAL_SCHEMA,
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
    from npc_manager import (
    NPCManager,
    JsonNPCRepository,
    build_turn_context,
    validate_proposal,
    NPC_BASE_SYSTEM_PROMPT,
    build_npc_turn_input_json,
    NPC_PROPOSAL_SCHEMA,
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


class _LLMProfileGenerator:
    def generate(self, requirement):
        payload = {
            "requirement_id": requirement.requirement_id,
            "purpose": requirement.purpose,
            "background_requirement": requirement.background_requirement,
            "relation_to_yangjian": requirement.relation_to_yangjian,
            "relation_to_user": requirement.relation_to_user,
            "current_goal": requirement.current_goal,
            "constraints": list(requirement.constraints),
        }
        raw = llm.call(
            agent_id="npc/profile_generator",
            system=(
                "你负责生成简洁的动态NPC档案。只输出JSON，字段为"
                "name、public_role、short_background、expression_style。"
                "不得添加剧情秘密、结局或用户未提供的关键设定。"
            ),
            messages=[{
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }],
            temperature=0.5,
            max_tokens=500,
        )
        try:
            data = json.loads(_extract_json(raw))
        except (json.JSONDecodeError, TypeError):
            data = {}
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", requirement.requirement_id)
        function = NarrativeFunction(requirement.narrative_function.value)
        return NPCProfile(
            npc_id=f"npc_{safe_id}",
            status=NPCStatus.READY,
            name=str(data.get("name") or f"NPC-{safe_id}"),
            public_role=str(data.get("public_role") or requirement.purpose),
            short_background=str(
                data.get("short_background") or requirement.background_requirement
            ),
            current_goal=requirement.current_goal,
            relation_to_yangjian=requirement.relation_to_yangjian,
            relation_to_user=requirement.relation_to_user,
            expression_style=str(data.get("expression_style") or "简洁、符合身份"),
            supported_functions=(function,),
            reusable=requirement.reusable,
        )


def _get_manager() -> NPCManager:
    global _manager, _manager_path
    current_path = runtime_context.scoped_path(NPC_STORE_PATH)
    if _manager is None or _manager_path != current_path:
        _manager = NPCManager(
            repository=JsonNPCRepository(current_path),
            profile_generator=_LLMProfileGenerator(),
            runtime=None,            # 同步实现走下面自己的 LLM 调用
        )
        _manager_path = current_path
    return _manager


# ── 生命周期管理 ──────────────────────────────────────────


def acquire_for_side_arc(requirement: Any, side_arc_id: str) -> str | None:
    """为副线获取/创建 NPC。"""
    mgr = _get_manager()
    try:
        # manager.py 的 acquire 接受 NPCRequirement
        record = mgr.acquire(requirement)
        return record.profile.npc_id
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


def act_for_task(npc_id: str, task: dict[str, Any], recent_events: list[str]) -> dict[str, Any]:
    """
    为单个 NPC 执行一次回合。

    Args:
        npc_id: NPC ID
        task: Director 下发的 npc_task
        recent_events: 最近事件摘要

    Returns:
        {"actions": [...], "dialogues": [...], "npc_id": npc_id}
    """
    mgr = _get_manager()
    record = mgr.repository.get(npc_id)
    if not record:
        return {"actions": [], "dialogues": [f"【{npc_id} 不在场】"], "npc_id": npc_id}

    # 1. 构建 director task
    dt = DirectorTask(
        task_id=npc_id,
        source=TaskSource.DIRECTOR_TASK,
        source_reference=task.get("source_reference", "room_runtime"),
        objective=task.get("objective", ""),
        visible_events=tuple(task.get("visible_events", []) + recent_events),
        known_facts=tuple(task.get("known_facts", [])),
        allowed_actions=tuple(task.get("allowed_actions", [])),
        must_not=tuple(task.get("must_not", [])),
    )

    # 2. 构建过滤后的回合上下文
    turn_ctx = build_turn_context(record, dt)

    # 3. 构建 NPC prompt
    npc_input = build_npc_turn_input_json(turn_ctx)
    system = NPC_BASE_SYSTEM_PROMPT

    # 4. 调 LLM
    raw = llm.call(
        agent_id=f"npc/{npc_id}",
        system=system,
        messages=[{"role": "user", "content": npc_input}],
        temperature=0.7,
        max_tokens=800,
    )

    # 5. 解析 NPC 提议
    proposal = _parse_proposal(raw, record.profile.npc_id)

    # 6. 校验提议
    validation = validate_proposal(record, dt, proposal)
    if not validation.is_valid:
        # 校验失败时返回安全默认
        return {
            "actions": [],
            "dialogues": [f"【{record.profile.name or npc_id}静静地站着】"],
            "npc_id": npc_id,
        }

    # 7. 拆分输出
    actions = []
    dialogues = []
    if proposal.action:
        actions.append(proposal.action)
    if proposal.utterance:
        dialogues.append(proposal.utterance)

    return {
        "actions": actions,
        "dialogues": dialogues,
        "npc_id": npc_id,
        "proposed_effects": list(proposal.proposed_effects),
    }


def _parse_proposal(raw: str, npc_id: str) -> Any:
    """解析 NPC 的 JSON 提议输出。"""
    text = raw.strip()
    # 尝试提取 JSON
    for prefix in ("```json", "```"):
        if prefix in text:
            text = text.split(prefix, 1)[1]
            text = text.rsplit("```", 1)[0]
            break
    try:
        data = json.loads(text.strip())
        return NPCProposal(
            npc_id=npc_id,
            intent=data.get("intent", ""),
            utterance=data.get("utterance", ""),
            action=data.get("action", ""),
            proposed_effects=tuple(data.get("proposed_effects", [])),
            proactive=bool(data.get("proactive", False)),
        )
    except (json.JSONDecodeError, Exception):
        # 回退：将全部文本作 dialogue
        return NPCProposal(
            npc_id=npc_id,
            intent="respond",
            utterance=text,
            action="",
            proposed_effects=(),
        )


def _extract_json(raw: str) -> str:
    text = raw.strip()
    for prefix in ("```json", "```"):
        if prefix in text:
            text = text.split(prefix, 1)[1]
            text = text.rsplit("```", 1)[0]
            break
    return text.strip()


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
