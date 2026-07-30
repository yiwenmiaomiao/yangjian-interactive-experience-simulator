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

import sys, os, json
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm

from npc_manager import (
    NPCManager,
    InMemoryNPCRepository,
    build_turn_context,
    validate_proposal,
    NPC_BASE_SYSTEM_PROMPT,
    build_npc_turn_input_json,
    NPC_PROPOSAL_SCHEMA,
    DirectorTask,
)

# 全局 NPC Manager 实例
_manager: NPCManager | None = None


def _get_manager() -> NPCManager:
    global _manager
    if _manager is None:
        _manager = NPCManager(
            repository=InMemoryNPCRepository(),
            profile_generator=None,  # 暂无 profile generator，用静态创建
            runtime=None,            # 同步实现走下面自己的 LLM 调用
        )
    return _manager


# ── 生命周期管理 ──────────────────────────────────────────


def acquire_for_side_arc(requirement: Any, side_arc_id: str) -> str | None:
    """为副线获取/创建 NPC。"""
    mgr = _get_manager()
    try:
        # manager.py 的 acquire 接受 NPCRequirement
        record = mgr.acquire(requirement)
        return record.record_id
    except Exception:
        return None


def activate(npc_id: str) -> bool:
    return _get_manager().activate(npc_id)


def deactivate(npc_id: str) -> bool:
    return _get_manager().deactivate(npc_id)


def complete(npc_id: str) -> bool:
    return _get_manager().complete(npc_id)


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
        objective=task.get("objective", ""),
        allowed_actions=tuple(task.get("allowed_actions", [])),
        must_not=tuple(task.get("must_not", [])),
    )

    # 2. 构建过滤后的回合上下文
    visible_events = task.get("visible_events", []) + recent_events
    turn_ctx = build_turn_context(
        record=record,
        visible_events=visible_events,
        director_task=dt,
    )

    # 3. 构建 NPC prompt
    npc_input = build_npc_turn_input_json(turn_ctx)
    system = NPC_BASE_SYSTEM_PROMPT

    # 4. 调 LLM
    raw = llm.call(
        system=system,
        messages=[{"role": "user", "content": json.dumps(npc_input, ensure_ascii=False)}],
        temperature=0.7,
        max_tokens=800,
    )

    # 5. 解析 NPC 提议
    proposal = _parse_proposal(raw, record.profile.npc_id)

    # 6. 校验提议
    validation = validate_proposal(proposal, record)
    if not validation.passed:
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
        from npc_manager import NPCProposal
        return NPCProposal(
            npc_id=npc_id,
            intent=data.get("intent", ""),
            utterance=data.get("utterance", ""),
            action=data.get("action", ""),
            proposed_effects=tuple(data.get("proposed_effects", [])),
        )
    except (json.JSONDecodeError, Exception):
        # 回退：将全部文本作 dialogue
        from npc_manager import NPCProposal
        return NPCProposal(
            npc_id=npc_id,
            intent="respond",
            utterance=text,
            action="",
            proposed_effects=(),
        )


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
