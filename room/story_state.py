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

from yangjian_story_generator.models import (
    NPCProfileSpec,
    StoryPlan,
    StoryBeat,
    SideArc,
)

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
        # beat goal 检测
        "beat_goal": "",
        "beat_max_turns": 6,
        "beat_goal_met": False,
        # recovery 弧
        "in_recovery": False,
        "recovery_arc_id": None,
        "recovery_rejoin_target": None,
        "recovery_sub_goal": "",
        "recovery_max_turns": 4,
        "recovery_sub_goal_met": False,
        # 杨戬对用户的关系状态
        "relationship": {
            "trust": 1,
            "respect": 0,
            "closeness": 1,
            "wariness": 1,
            "history": [],
        },
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
                    "beat_goal": state.get("beat_goal", ""),
                    "beat_max_turns": state.get("beat_max_turns", 6),
                    "beat_tick_counter": state.get("beat_tick_counter", 0),
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
                    "in_recovery": True,
                    "recovery_sub_goal": state.get("recovery_sub_goal", ""),
                    "recovery_max_turns": state.get("recovery_max_turns", 4),
                    "recovery_tick_counter": state.get("beat_tick_counter", 0),
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
            "relationship_requirements": getattr(t, "relationship_requirements", None),
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
        "beat_goal": getattr(beat, "goal", "") or state.get("beat_goal", "") or "",
        "beat_max_turns": getattr(beat, "max_turns", 0) or state.get("beat_max_turns", 6),
        "beat_tick_counter": state.get("beat_tick_counter", 0),
        "participants": list(beat.participants),
        "allowed_information": list(beat.allowed_information),
        "forbidden_reveals": list(beat.forbidden_reveals),
        "available_transitions": transitions,
        "available_side_arcs": available_side_arcs,
        "npc_requirement_ids": list(beat.npc_requirement_ids),
        "main_progress": state.get("main_progress", 0.0),
        "flags": dict(state.get("flags", {})),
        "consequences": list(state.get("selected_branch_consequences", [])),
        "relationship_checkpoint": getattr(beat, "relationship_checkpoint", None),
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


def get_npc_profile(requirement_id: str) -> NPCProfileSpec | None:
    """Return the complete generated profile for a requirement.

    Older plans are adapted deterministically so runtime never calls a profile
    generation LLM.  Re-saving such a plan with Story Generator is recommended.
    """
    plan = get_plan()
    if not plan:
        return None
    for profile in plan.npc_profiles:
        if profile.requirement_id == requirement_id:
            return profile
    for arc in plan.side_arcs:
        for requirement in arc.npc_requirements:
            if requirement.requirement_id != requirement_id:
                continue
            return NPCProfileSpec(
                profile_id=f"profile_{requirement.requirement_id}",
                requirement_id=requirement.requirement_id,
                narrative_function=requirement.narrative_function,
                name=f"NPC-{requirement.requirement_id}",
                public_role=requirement.purpose,
                personality=("符合其公开身份和当前目标",),
                background=(
                    requirement.background_requirement
                    or f"为{requirement.purpose}进入故事"
                ),
                expression_style="简洁、符合身份",
                goals=(requirement.current_goal or requirement.purpose,),
                relation_to_yangjian=requirement.relation_to_yangjian,
                relation_to_user=requirement.relation_to_user,
                knows=requirement.must_know,
                must_not_know=requirement.must_not_know,
                behavior_boundaries=requirement.constraints,
                story_bindings=(
                    requirement.story_id,
                    requirement.side_arc_id,
                ),
                reusable=requirement.reusable,
            )
    return None


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
    state["beat_goal_met"] = False

    # recovery 弧退出：推进到非 recovery 的 beat 时，清除 recovery 状态
    if state.get("in_recovery"):
        recovery_beats = state.get("_recovery_beats", [])
        is_recovery_beat = any(
            b.get("beat_id") == target_beat_id for b in recovery_beats
        )
        if not is_recovery_beat:
            state["in_recovery"] = False
            state["recovery_arc_id"] = None
            state["_recovery_beats"] = []
            state["recovery_rejoin_target"] = None
            state["recovery_sub_goal"] = ""
            state["recovery_sub_goal_met"] = False

    # 设置新 beat 的 goal 和 max_turns
    beat = _find_beat(plan, target_beat_id)
    if beat:
        # 从 story plan 读取 goal 和 max_turns（如果有）
        beat_goal = getattr(beat, "goal", "") or getattr(beat, "beat_goal", "")
        beat_max_turns = getattr(beat, "max_turns", 0) or 0
        state["beat_goal"] = beat_goal
        state["beat_max_turns"] = beat_max_turns if beat_max_turns > 0 else 6
    else:
        # recovery beat: goal 从 beat purpose 推导
        recovery_beats = state.get("_recovery_beats", [])
        for rb in recovery_beats:
            if rb.get("beat_id") == target_beat_id:
                state["beat_goal"] = rb.get("purpose", "")
                state["beat_max_turns"] = rb.get("max_turns", 4)
                break

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


# ── beat goal 检测 ─────────────────────────────────────────


def check_beat_goal(state: dict[str, Any], goal_met: bool) -> bool:
    """Director resolve 调用：标记当前 beat 的 goal 是否达成。

    返回 True 表示 goal 达成，Room 应推进到下一个 beat。
    """
    state["beat_goal_met"] = goal_met
    save_state(state)
    return goal_met


def check_beat_max_turns(state: dict[str, Any]) -> bool:
    """检查当前 beat 是否已达到最大轮次。

    返回 True 表示已达到 max_turns，需要 director 判定 goal 是否达成，
    未达成则进入 recovery。
    """
    max_turns = state.get("beat_max_turns", 6)
    if max_turns <= 0:
        return False
    return state.get("beat_tick_counter", 0) >= max_turns


def ensure_beat_goal(state: dict[str, Any]) -> str | None:
    """如果 beat_goal 为空，返回需要 director 动态生成的信号。"""
    if not state.get("beat_goal"):
        return "needs_generation"
    return None


# ── recovery 弧管理 ─────────────────────────────────────────


def enter_recovery_arc(
    state: dict[str, Any],
    recovery_arc_id: str,
    recovery_beats: list[dict],
    rejoin_target: str,
    sub_goal: str = "",
    max_turns: int = 4,
) -> None:
    """进入回归弧模式。

    sub_goal: 本次 recovery 的子目标（director resolve 每回合检测是否达成）
    max_turns: 最大回合数，到了仍没达成子目标则强行退出
    """
    state["in_recovery"] = True
    state["recovery_arc_id"] = recovery_arc_id
    state["_recovery_beats"] = recovery_beats
    state["recovery_rejoin_target"] = rejoin_target
    state["recovery_sub_goal"] = sub_goal
    state["recovery_max_turns"] = max_turns
    state["recovery_sub_goal_met"] = False
    state["current_beat_id"] = recovery_beats[0]["beat_id"] if recovery_beats else rejoin_target
    state["beat_tick_counter"] = 0
    save_state(state)


def check_recovery_sub_goal(state: dict[str, Any], sub_goal_met: bool) -> bool:
    """Director resolve 调用：标记 recovery 子目标是否达成。

    返回 True 表示子目标达成，应退出 recovery 回到主线。
    """
    state["recovery_sub_goal_met"] = sub_goal_met
    save_state(state)
    return sub_goal_met


def check_recovery_max_turns(state: dict[str, Any]) -> bool:
    """检查 recovery 是否已达到最大回合数。

    返回 True 表示需要强行退出 recovery（narrator 写过渡剧情衔接下一 beat）。
    """
    max_turns = state.get("recovery_max_turns", 4)
    if max_turns <= 0:
        return False
    return state.get("beat_tick_counter", 0) >= max_turns


def exit_recovery_arc(state: dict[str, Any]) -> str | None:
    """退出回归弧，回到主线。返回 rejoin_target（供 Room 调 narrator 写过渡）。"""
    target = state.get("recovery_rejoin_target")
    state["in_recovery"] = False
    state["recovery_arc_id"] = None
    state["recovery_rejoin_target"] = None
    state["recovery_sub_goal"] = ""
    state["recovery_sub_goal_met"] = False
    state["_recovery_beats"] = []
    save_state(state)
    return target
