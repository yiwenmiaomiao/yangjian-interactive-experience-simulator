"""
故事计划运行状态管理器
管理当前剧情进度、当前 beat、已设旗标、已解锁副线。
Room 拥有此状态，导演只能消费 Room 提供的当前可用节点。
"""
from __future__ import annotations

import json
import os
from typing import Any
import runtime_context

from yangjian_story_generator.models import StoryPlan, StoryBeat, SideArc

PROFILE_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))
STORY_STATE_PATH = os.path.join(PROFILE_DIR, "story_state.json")
DEFAULT_PLAN_PATH = os.path.join(PROFILE_DIR, "contexts/story_plan_story_1.json")

# 引入 story_plan 解析
_plan: StoryPlan | None = None


def load_plan(path: str | None = None) -> StoryPlan | None:
    """加载故事计划。"""
    from yangjian_story_generator.codec import story_plan_from_json

    global _plan
    p = path or DEFAULT_PLAN_PATH
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        _plan = story_plan_from_json(f.read())
    return _plan


def get_plan() -> StoryPlan | None:
    return _plan


# ── 运行状态 ──────────────────────────────────────────────


def default_state() -> dict[str, Any]:
    return {
        "status": "inactive",
        "current_arc": "main",
        "current_beat_id": "",
        "current_beat_index": -1,
        "main_progress": 0.0,
        "flags": {},
        "selected_branch_consequences": [],
        "completed_beats": [],
        "completed_endings": [],
        "unlocked_side_arcs": [],
        "active_side_arcs": [],
        "beat_tick_counter": 0,
        # 偏离检测
        "deviation_count": 0,
        "consecutive_deviation": 0,
        "last_deviation_at": None,
        "in_recovery": False,
        "recovery_arc_id": None,
        "recovery_rejoin_target": None,
    }


def load_state() -> dict[str, Any]:
    path = runtime_context.scoped_path(STORY_STATE_PATH)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default_state()


def save_state(state: dict[str, Any]) -> None:
    with open(runtime_context.scoped_path(STORY_STATE_PATH), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def reset_state() -> dict[str, Any]:
    s = default_state()
    save_state(s)
    return s


def activate_plan() -> dict[str, Any]:
    """激活故事计划，设置起始 beat。"""
    plan = get_plan()
    if not plan:
        return default_state()
    state = default_state()
    state["status"] = "active"
    state["current_beat_id"] = plan.main_arc.start_beat_id
    state["current_beat_index"] = 0
    save_state(state)
    return state


# ── 当前 beat 信息（给导演看） ─────────────────────────────


def get_current_beat_info(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回当前 beat 的信息（不包含完整故事图）。"""
    plan = get_plan()
    if not plan:
        load_plan()
        plan = get_plan()
    if not plan:
        return {"error": "no_plan"}
    if state is None:
        state = load_state()

    current_id = state.get("current_beat_id", "")

    # 先检查是否在回归弧中
    if state.get("in_recovery"):
        recovery_beats = state.get("_recovery_beats", [])
        for b in recovery_beats:
            if b["beat_id"] == current_id:
                transitions = b.get("transitions", [])
                return {
                    "story_id": plan.story_id,
                    "current_beat_id": b["beat_id"],
                    "beat_purpose": b["purpose"],
                    "participants": b.get("participants", ["user", "yangjian"]),
                    "allowed_information": b.get("allowed_information", []),
                    "forbidden_reveals": b.get("forbidden_reveals", []),
                    "available_transitions": [
                        {"transition_id": t.get("transition_id", "rejoin"),
                         "target_id": t.get("target_id", state.get("recovery_rejoin_target", "")),
                         "preserved_consequences": t.get("preserved_consequences", [])}
                        for t in transitions
                    ],
                    "available_side_arcs": [],
                    "npc_requirement_ids": [],
                    "main_progress": state.get("main_progress", 0.0),
                    "flags": dict(state.get("flags", {})),
                    "consequences": list(state.get("selected_branch_consequences", [])),
                    "beat_tick_counter": state.get("beat_tick_counter", 0),
                        "in_recovery": True,
                    }
    beat = _find_beat(plan, current_id)
    if not beat:
        return {"error": f"beat_not_found:{current_id}"}

    # 找出可用 transition（不含条件过滤）
    transitions = [
        {
            "transition_id": t.transition_id,
            "target_id": t.target_id,
            "preserved_consequences": list(t.preserved_consequences),
        }
        for t in beat.transitions
    ]

    # 已解锁副线
    unlocked = state.get("unlocked_side_arcs", [])
    available_side_arcs = []
    for arc in plan.side_arcs:
        if arc.arc_id in unlocked and arc.arc_id not in state.get("active_side_arcs", []):
            available_side_arcs.append({
                "arc_id": arc.arc_id,
                "purpose": arc.purpose,
            })

    return {
        "story_id": plan.story_id,
        "current_beat_id": beat.beat_id,
        "beat_purpose": beat.purpose,
        "participants": list(beat.participants),
        "allowed_information": list(beat.allowed_information),
        "forbidden_reveals": list(beat.forbidden_reveals),
        "available_transitions": transitions,
        "available_side_arcs": available_side_arcs,
        "npc_requirement_ids": list(beat.npc_requirement_ids),
        "main_progress": state.get("main_progress", 0.0),
        "flags": dict(state.get("flags", {})),
        "consequences": list(state.get("selected_branch_consequences", [])),
        "beat_tick_counter": state.get("beat_tick_counter", 0),
    }


def _find_beat(plan: StoryPlan, beat_id: str) -> StoryBeat | None:
    """在主线或副线中查找 beat。"""
    for beat in plan.main_arc.beats:
        if beat.beat_id == beat_id:
            return beat
    for arc in plan.side_arcs:
        for beat in arc.beats:
            if beat.beat_id == beat_id:
                return beat
    return None


def _find_arc(plan: StoryPlan, beat_id: str) -> str | None:
    for beat in plan.main_arc.beats:
        if beat.beat_id == beat_id:
            return "main"
    for arc in plan.side_arcs:
        for beat in arc.beats:
            if beat.beat_id == beat_id:
                return arc.arc_id
    return None


def get_current_npc_requirements(
    state: dict[str, Any] | None = None,
) -> tuple[Any, ...]:
    """Return full NPC requirements for Room only; never expose them to agents."""
    plan = get_plan()
    if not plan:
        return ()
    state = state or load_state()
    beat = _find_beat(plan, state.get("current_beat_id", ""))
    if not beat:
        return ()
    required_ids = set(beat.npc_requirement_ids)
    return tuple(
        requirement
        for arc in plan.side_arcs
        for requirement in arc.npc_requirements
        if requirement.requirement_id in required_ids
    )


# ── 状态变更（由 Room 调用，导演不下达） ─────────────────


def advance_beat(state: dict[str, Any], target_beat_id: str, consequences: list[str] | None = None) -> dict[str, Any]:
    """推进到下一个 beat。"""
    plan = get_plan()
    if not plan:
        return state

    # 记录已完成 beat
    current = state.get("current_beat_id", "")
    if current:
        completed = state.setdefault("completed_beats", [])
        if current not in completed:
            completed.append(current)

    # 检查是否是结局
    ends = [e.ending_id for e in plan.main_arc.endings]
    for arc in plan.side_arcs:
        ends.extend(e.ending_id for e in arc.endings)
    if target_beat_id in ends:
        completed_ends = state.setdefault("completed_endings", [])
        if target_beat_id not in completed_ends:
            completed_ends.append(target_beat_id)
        if _find_arc(plan, target_beat_id) == "main":
            state["status"] = "completed"

    state["current_beat_id"] = target_beat_id
    state["beat_tick_counter"] = 0

    # 保存分支后果
    if consequences:
        existing = state.setdefault("selected_branch_consequences", [])
        existing.extend(consequences)

    # 更新主线进度
    if _find_arc(plan, target_beat_id) == "main":
        main_beats = len(plan.main_arc.beats)
        main_ends = len(plan.main_arc.endings)
        total = main_beats + main_ends
        completed_count = len(state.get("completed_beats", []))
        state["main_progress"] = min(1.0, (completed_count + 1) / max(1, total))

    save_state(state)
    return state


def check_and_unlock_side_arcs(state: dict[str, Any]) -> list[str]:
    """检查主线进度是否解锁了新的副线。"""
    plan = get_plan()
    if not plan:
        return []
    newly_unlocked = []
    for arc in plan.side_arcs:
        if arc.arc_id in state.get("unlocked_side_arcs", []):
            continue
        rule = arc.unlock
        passed = True
        if rule.minimum_main_progress > 0:
            if state.get("main_progress", 0.0) < rule.minimum_main_progress:
                passed = False
        for m in rule.required_milestones:
            if m not in plan.main_arc.milestones:
                continue
            # 检查里程碑是否完成——即主线进度超过该里程碑对应的 beat
            milestone_index = plan.main_arc.milestones.index(m)
            beats_per_ms = max(1, len(plan.main_arc.beats) // max(1, len(plan.main_arc.milestones)))
            if state.get("main_progress", 0.0) < (milestone_index + 1) * beats_per_ms / len(plan.main_arc.beats):
                passed = False
        for f, expected in rule.required_flags.items() if hasattr(rule.required_flags, 'items') else []:
            if state.get("flags", {}).get(f) != expected:
                passed = False
        if passed:
            state.setdefault("unlocked_side_arcs", []).append(arc.arc_id)
            newly_unlocked.append(arc.arc_id)
    if newly_unlocked:
        save_state(state)
    return newly_unlocked


def set_flag(state: dict[str, Any], key: str, value: Any = True) -> None:
    state.setdefault("flags", {})[key] = value
    save_state(state)


def increment_beat_tick(state: dict[str, Any]) -> None:
    state["beat_tick_counter"] = state.get("beat_tick_counter", 0) + 1
    save_state(state)


# ── 偏离检测 ──────────────────────────────────────────────


DEVIATION_THRESHOLD = 2  # 连续偏离多少次触发回归


def record_deviation(state: dict[str, Any], user_message: str) -> bool:
    """记录一次偏离。返回 True 表示需要触发回归。"""
    from datetime import datetime
    state["deviation_count"] = state.get("deviation_count", 0) + 1
    state["consecutive_deviation"] = state.get("consecutive_deviation", 0) + 1
    state["last_deviation_at"] = datetime.now().isoformat()
    save_state(state)
    return state["consecutive_deviation"] >= DEVIATION_THRESHOLD


def clear_deviation(state: dict[str, Any]) -> None:
    """用户回到正轨时清零偏离计数。"""
    state["consecutive_deviation"] = 0
    save_state(state)


# ── 回归弧管理 ────────────────────────────────────────────


def enter_recovery_arc(state: dict[str, Any], recovery_arc_id: str, recovery_beats: list[dict], rejoin_target: str) -> None:
    """进入回归弧模式。"""
    state["in_recovery"] = True
    state["recovery_arc_id"] = recovery_arc_id
    state["_recovery_beats"] = recovery_beats
    state["recovery_rejoin_target"] = rejoin_target
    state["current_beat_id"] = recovery_beats[0]["beat_id"] if recovery_beats else rejoin_target
    state["beat_tick_counter"] = 0
    state["consecutive_deviation"] = 0
    save_state(state)


def exit_recovery_arc(state: dict[str, Any]) -> None:
    """退出回归弧，回到主线。"""
    state["in_recovery"] = False
    state["recovery_arc_id"] = None
    target = state.get("recovery_rejoin_target")
    if target:
        state["current_beat_id"] = target
    state["recovery_rejoin_target"] = None
    state["beat_tick_counter"] = 0
    save_state(state)
