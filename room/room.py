"""
Room 主循环 — 杨戬故事模拟器的多Agent编排器。

支持两种模式：
1. 传统模式：基于 story_engine 读取 markdown phase 文件
2. 故事计划模式：基于 StoryPlan 的 beat 推进

流程：
1. 加载世界状态 + 故事计划（如有）
2. 调用织梦者（导演Agent）裁决
3. 按裁决的 order 依次调用各 Agent
4. 更新世界状态 + 故事进度
5. 返回故事文本
"""
from __future__ import annotations

import os
import sys
import json
import traceback
import threading
from functools import wraps
from typing import Any, Mapping

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_context, state_manager, story_engine
if __package__:
    from . import (
        contracts,
        director,
        narrator,
        npc_manager_runtime as npc,
        yangjian,
    )
else:
    import director, narrator, yangjian
    import npc_manager_runtime as npc
    import contracts

PROFILE_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))

# 是否启用故事计划模式
_story_plan_active = False
_TICK_LOCK = threading.RLock()


def _serialized_tick(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _TICK_LOCK:
            lock_path = os.path.join(PROFILE_DIR, ".room_tick.lock")
            with runtime_context.process_lock(lock_path):
                return function(*args, **kwargs)
    return wrapped


# ── 故事计划管理 ──────────────────────────────────────────


def activate_story_plan(story_id: str | None = None) -> str:
    """激活一个故事计划，返回状态信息。"""
    global _story_plan_active
    import story_state

    if story_id:
        path = os.path.join(PROFILE_DIR, f"contexts/story_plan_{story_id}.json")
        loaded = story_state.load_plan(path)
    else:
        loaded = story_state.load_plan()

    if not loaded:
        return "故事计划文件未找到"

    plan = story_state.get_plan()
    state = story_state.activate_plan()
    summary = {
        "story_id": plan.story_id,
        "theme": plan.theme,
        "premise": plan.premise,
        "beats": len(plan.main_arc.beats),
        "side_arcs": len(plan.side_arcs),
        "endings": len(plan.main_arc.endings),
    }
    _story_plan_active = True
    return json.dumps(summary, ensure_ascii=False)


def deactivate_story_plan() -> None:
    global _story_plan_active
    _story_plan_active = False
    import story_state
    story_state.reset_state()


def story_plan_status() -> str:
    import story_state
    state = story_state.load_state()
    plan = story_state.get_plan()
    if not plan:
        return json.dumps({"active": False})
    beat = story_state.get_current_beat_info(state)
    return json.dumps({
        "active": _story_plan_active,
        "status": state.get("status"),
        "current_beat_id": state.get("current_beat_id"),
        "main_progress": state.get("main_progress"),
        "beat_info": beat.get("beat_purpose", "") if not beat.get("error") else beat["error"],
        "completed_beats": len(state.get("completed_beats", [])),
        "flags": state.get("flags", {}),
        "in_recovery": state.get("in_recovery", False),
        "deviation_count": state.get("consecutive_deviation", 0),
    }, ensure_ascii=False)


# ── 回归剧情生成 ──────────────────────────────────────────


def _trigger_recovery(ss_state: dict[str, Any], user_message: str, decision: dict[str, Any]) -> None:
    """用户偏离剧情时生成短回归弧。"""
    import story_state as ss
    
    # 构造回归 prompt
    plan = ss.get_plan()
    if not plan:
        return
    
    current_beat_id = ss_state.get("current_beat_id", "")
    current_beat_purpose = ""
    for beat in plan.main_arc.beats:
        if beat.beat_id == current_beat_id:
            current_beat_purpose = beat.purpose
            break
    
    # 找下个主线 beat 作为回归目标
    rejoin_target = ""
    for i, beat in enumerate(plan.main_arc.beats):
        if beat.beat_id == current_beat_id:
            if i + 1 < len(plan.main_arc.beats):
                rejoin_target = plan.main_arc.beats[i + 1].beat_id
            break
    if not rejoin_target and plan.main_arc.beats:
        rejoin_target = plan.main_arc.beats[0].beat_id
    
    recovery_id = f"recovery_{current_beat_id}"
    
    # 构建系统提示
    system = """你是一个短回归剧情架构师。
用户偏离了主线剧情。你需要生成一个极短的回归弧（1~3个beat），自然地把用户引回主线。

## 规则
1. 承认用户刚才做的行为，不能假装没发生
2. 利用用户当前关注的人、物或事件作为回归入口
3. 用户不能感觉被强制纠正
4. 回归弧结束后自动回到主线
5. 不要预写对白
6. 输出 JSON 格式的 beats

## 输出格式
{
  "beats": [
    {
      "beat_id": "r1",
      "purpose": "利用用户当前关注点自然引向主线",
      "participants": ["user", "yangjian"],
      "allowed_information": [],
      "forbidden_reveals": ["main_ending"],
      "transitions": [
        {"transition_id": "r1_to_r2", "target_id": "r2", "preserved_consequences": ["用户刚才的行为"]}
      ]
    }
  ]
}"""

    user_prompt = f"""## 用户最新消息
{user_message[:200]}

## 当前主线 Beat
ID: {current_beat_id}
目的: {current_beat_purpose[:200]}

## 主线目标
{plan.main_arc.goal[:200]}

## 回归目标 Beat
{rejoin_target}

## 要求
生成 1~2 个回归 beat，利用用户当前关注点自然引回主线。"""
    
    # 调用模型
    room_dir = os.path.join(PROFILE_DIR, "room")
    sys.path.insert(0, room_dir)
    import llm as room_llm
    raw = room_llm.call(
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.7,
        max_tokens=2000,
    )
    
    # 解析
    try:
        text = raw.strip()
        for prefix in ("```json", "```"):
            if prefix in text:
                text = text.split(prefix, 1)[1]
                text = text.rsplit("```", 1)[0]
                break
        recovery_data = json.loads(text.strip())
        beats_raw = recovery_data.get("beats", [])
        
        # 验证 beats
        if _valid_recovery_beats(beats_raw, rejoin_target):
            recovery_beats = []
            for b in beats_raw:
                recovery_beats.append({
                    "beat_id": b["beat_id"],
                    "purpose": b["purpose"],
                    "participants": b.get("participants", ["user", "yangjian"]),
                    "allowed_information": b.get("allowed_information", []),
                    "forbidden_reveals": b.get("forbidden_reveals", ["main_ending"]),
                    "transitions": b.get("transitions", [{"transition_id": f"{b['beat_id']}_to_rejoin", "target_id": rejoin_target}]),
                })
            
            ss.enter_recovery_arc(ss_state, recovery_id, recovery_beats, rejoin_target)
            # 更新 beat_info 缓存让导演使用
            bi = ss.get_current_beat_info(ss_state)
            director.set_story_context(bi)
    except (json.JSONDecodeError, KeyError) as e:
        pass  # 如果生成失败，静默继续当前 beat


def _valid_recovery_beats(beats: Any, rejoin_target: str) -> bool:
    if not isinstance(beats, list) or not 1 <= len(beats) <= 2:
        return False
    ids = [beat.get("beat_id") for beat in beats if isinstance(beat, dict)]
    if len(ids) != len(beats) or len(set(ids)) != len(ids) or not all(ids):
        return False
    allowed_targets = {*ids, rejoin_target}
    for beat in beats:
        if not str(beat.get("purpose", "")).strip():
            return False
        for transition in beat.get("transitions", []):
            if transition.get("target_id") not in allowed_targets:
                return False
    return True


# ── 主循环 ──────────────────────────────────────────────────


@_serialized_tick
def tick(
    user_message=None,
    source="cron",
    *,
    user_id: str = "default",
    thread_id: str = "default",
):
    """
    执行一个 Room Tick。
    
    Args:
        user_message: 用户输入文本，None 表示定时推动
        source: 触发源 "cron" 或 "user"
    
    Returns:
        dict: {"ok": bool, "output": [...], "state": {...}, "decision": {...}}
    """
    global _story_plan_active
    identity_token = runtime_context.set_identity(user_id, thread_id)
    try:
        state = state_manager.load()
        if user_message:
            _capture_explicit_preferences(user_message, user_id)

        import story_state as ss
        plan = ss.get_plan() or ss.load_plan()
        persisted_story = ss.load_state()
        _story_plan_active = bool(
            plan and persisted_story.get("status") == "active"
        )
        if _story_plan_active:
            director.set_story_context(
                ss.get_current_beat_info(persisted_story)
            )

        # ── 两阶段调度：故事计划模式走 DIRECT → RESOLVE ──
        if _story_plan_active:
            return _tick_two_stage(state, user_message, source)

        if os.environ.get("YANGJIAN_ALLOW_LEGACY_MODE") != "1":
            return {
                "ok": False,
                "error": "story_plan_not_active",
                "output": [{
                    "role": "系统",
                    "text": "【故事计划未激活，已阻止旧版导演直接修改状态】",
                }],
            }

        # ── 传统模式（仅显式兼容开关启用） ──
        decision = director.decide(state, user_message)
        
        outputs = []
        
        for role in decision.get("order", []):
            if role == "旁白":
                text = narrator.speak(decision, state)
                outputs.append({"role": "旁白", "text": text})
            
            elif role == "杨戬":
                perception = state_manager.get_perception("yangjian", state, decision.get("outcome", ""))
                result = yangjian.act(decision, perception)
                for action in result.get("actions", []):
                    outputs.append({"role": "杨戬的动作", "text": action})
                for dialogue in result.get("dialogues", []):
                    outputs.append({"role": "杨戬", "text": dialogue})
            
            elif role.startswith("NPC_"):
                npc_name = role.replace("NPC_", "")
                perception = state_manager.get_perception(npc_name, state, decision.get("outcome", ""))
                result = npc.act(npc_name, decision, perception)
                for action in result.get("actions", []):
                    outputs.append({"role": f"{npc_name}的动作", "text": action})
                for dialogue in result.get("dialogues", []):
                    outputs.append({"role": npc_name, "text": dialogue})
            
            elif role == "用户":
                pass  # 等待真实用户输入
            
            else:
                outputs.append({"role": role, "text": f"【{role} 没有对应的 Agent】"})
        
        # 应用世界变更
        changes = decision.get("world_changes", {})
        if changes:
            state = state_manager.apply_changes(state, changes)
        
        # 推进 tick 计数器
        for sk, sv in state.get("stories", {}).items():
            if sv.get("triggered") or sv.get("phase", 0) > 0:
                sv["ticks_stalled"] = sv.get("ticks_stalled", 0) + 1
        
        story_changes = changes.get("stories", {})
        for sk, sv in state.get("stories", {}).items():
            if sk in story_changes and "phase" in story_changes.get(sk, {}):
                sv["ticks_stalled"] = 0
        
        # ── 统一事件日志（Room 是真相源） ──
        # 无论导演有没有写入 world_changes.event_log，Room 自己记录
        if user_message:
            state.setdefault("event_log", []).append(
                f"[tick{state.get('tick',0)}] 用户: {user_message[:120]}"
            )
        if outputs:
            for o in outputs:
                role = o.get("role", "")
                text = o.get("text", "")
                if text and role not in ("用户", "系统"):
                    state.setdefault("event_log", []).append(
                        f"[tick{state.get('tick',0)}] {role}: {text[:200]}"
                    )
        # 保留导演的事件记录（如果有）
        director_events = changes.get("public_event_log", changes.get("event_log", []))
        if director_events:
            for e in director_events:
                if isinstance(e, str):
                    state.setdefault("event_log", []).append(
                        f"[tick{state.get('tick',0)}] 事件: {e[:200]}"
                    )
        
        # 限制日志长度
        if len(state.get("event_log", [])) > 500:
            state["event_log"] = state["event_log"][-500:]
        
        # 推进故事计划状态（如有）
        if _story_plan_active:
            import story_state as ss
            ss_state = ss.load_state()
            if ss_state.get("status") == "active":
                ss.increment_beat_tick(ss_state)
                
                # 检查导演是否检测到偏离
                deviation_signal = decision.get("deviation_signal")
                if deviation_signal:
                    user_msg = user_message or ""
                    needs_recovery = ss.record_deviation(ss_state, user_msg)
                    if needs_recovery:
                        # 尝试兼容——如果导演说可以兼容，就不触发回归
                        compatible = decision.get("deviation_compatible", False)
                        if not compatible and not ss_state.get("in_recovery"):
                            _trigger_recovery(ss_state, user_msg, decision)
                else:
                    ss.clear_deviation(ss_state)
                
                # 检查副线解锁
                newly = ss.check_and_unlock_side_arcs(ss_state)
                if newly:
                    decision.setdefault("notes", []).append(f"副线解锁: {', '.join(newly)}")
        
        state["tick"] = state.get("tick", 0) + 1
        
        state_manager.save(state)
        
        return {
            "ok": True,
            "output": outputs,
            "state": state,
            "decision": decision,
        }
    
    except Exception as e:
        try:
            import llm as room_llm
            room_llm.clear_trace_context()
        except Exception:
            pass
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(e),
            "output": [{"role": "系统", "text": f"【Room 异常: {e}】"}],
        }
    finally:
        runtime_context.reset_identity(identity_token)


def tick_with_story(user_message=None, source="cron"):
    """带故事计划上下文的 tick。"""
    import story_state as ss
    
    result = tick(user_message, source)
    
    if _story_plan_active and result.get("ok"):
        ss_state = ss.load_state()
        if ss_state.get("status") == "active":
            beat_info = ss.get_current_beat_info(ss_state)
            result["story_beat"] = beat_info
    
    return result


# ── 两阶段故事计划 tick ──────────────────────────────────


def _tick_two_stage(state, user_message=None, source="cron"):
    """故事计划模式的两阶段 tick：DIRECT → Agent行动 → RESOLVE → Room保存。"""
    import story_state as ss

    # Phase 0: 更新 beat_info 缓存
    ss_state = ss.load_state()
    if ss_state.get("status") != "active":
        return _tick_traditional_fallback(state, user_message, source)

    # 初始化 Langfuse 日志上下文
    from langfuse_logger import LangfuseCtx, log_state_change, flush as lf_flush
    lf_ctx = LangfuseCtx(
        tick=state.get("tick", 0) + 1,
        story_id=ss_state.get("story_id", ss_state.get("current_beat_id", "story_1")),
        beat_id=ss_state.get("current_beat_id", ""),
    )
    import llm as room_llm
    room_llm.set_trace_context(lf_ctx)

    # 刷新导演上下文。Story Generator 的完整 NPC Profile 只交给
    # NPC Manager；Director 只能看到可引用的 profile_id。
    bi = ss.get_current_beat_info(ss_state)
    requirements = ss.get_current_npc_requirements(ss_state)
    profile_specs = {
        profile.profile_id: profile
        for requirement in requirements
        if (profile := ss.get_npc_profile(requirement.requirement_id)) is not None
    }
    requirement_by_profile = {
        profile.profile_id: requirement
        for requirement in requirements
        if (profile := ss.get_npc_profile(requirement.requirement_id)) is not None
    }
    registry = npc.registry_snapshot()
    active_npcs = [
        item["npc_id"]
        for item in registry.get("profiles", [])
        if item.get("status") == "active"
    ]
    bi = dict(bi)
    bi["active_npcs"] = active_npcs
    bi["npc_registry"] = registry
    bi["npc_profiles"] = [
        {
            "profile_id": profile.profile_id,
            "requirement_id": profile.requirement_id,
        }
        for profile in profile_specs.values()
    ]
    director.set_story_context(bi)

    # Phase 1: DIRECTOR DIRECT
    room_ref = contracts.AgentRef(
        agent_id="room", kind=contracts.AgentKind.ROOM
    )
    director_ref = contracts.AgentRef(
        agent_id="director", kind=contracts.AgentKind.DIRECTOR
    )
    direct_request = contracts.new_message(
        turn_id=f"turn_{state.get('tick', 0) + 1}",
        story_id=str(bi.get("story_id", "story_1")),
        beat_id=str(bi.get("current_beat_id", "")),
        phase=contracts.Phase.DIRECT,
        sender=room_ref,
        recipient=director_ref,
        message_type="director.direct.input",
        payload=contracts.DirectorDirectInput(
            user_event={
                "type": "user_message",
                "text": user_message or "",
            },
            story_cursor={
                "story_id": bi.get("story_id", "story_1"),
                "beat_id": bi.get("current_beat_id", ""),
                "purpose": bi.get("beat_purpose", ""),
            },
            world_snapshot=dict(state),
            available_actor_agents=tuple(
                [
                    contracts.AgentRef(
                        agent_id="yangjian",
                        kind=contracts.AgentKind.ACTOR,
                    ),
                    *[
                        contracts.AgentRef(
                            agent_id=npc_id,
                            kind=contracts.AgentKind.ACTOR,
                        )
                        for npc_id in active_npcs
                    ],
                ]
            ),
            npc_requirements=tuple(
                contracts.to_dict(requirement)
                for requirement in requirements
            ),
            npc_registry=registry,
            unlocked_transitions=tuple(
                bi.get("available_transitions", ())
            ),
            available_side_arcs=tuple(
                bi.get("available_side_arcs", ())
            ),
            recent_confirmed_events=tuple(
                state.get("confirmed_events", ())[-20:]
            ),
            liveness={
                "beat_tick_counter": bi.get("beat_tick_counter", 0),
            },
        ),
    )
    direct_response = director.handle_direct(direct_request)
    directive = contracts.to_dict(direct_response.payload)
    directive["current_story_id"] = bi.get("story_id", "story_1")
    directive["current_beat"] = bi.get("current_beat_id", "")

    # NPC Manager 不是 Agent。Room 在 Actor 执行前确定性应用 Director 命令。
    npc_command_results = []
    for command in directive.get("npc_commands", []):
        profile_id = str(command.get("profile_id", ""))
        requirement = requirement_by_profile.get(profile_id)
        npc_command_results.append(
            npc.execute_command(
                command,
                profile_spec=profile_specs.get(profile_id),
                story_id=bi.get("story_id", "story_1"),
                side_arc_id=(
                    requirement.side_arc_id if requirement is not None else ""
                ),
            )
        )

    public_history = list(contracts.published_history(state))
    turn_id = direct_request.turn_id
    if user_message:
        public_history.append(
            contracts.PublishedMessage(
                message_id=f"{turn_id}_user",
                turn_id=turn_id,
                role="用户",
                kind="dialogue",
                text=user_message,
            )
        )
    public_history_tuple = tuple(public_history)

    resolve_gate = directive.get("resolve_gate") or {}
    resolve_required = resolve_gate.get("required", True) is not False
    act_required = resolve_gate.get("act_required", True) is not False

    # Phase 2: Actor Pool 行动。Narrator 不在此对象池中。
    actor_results: list[dict[str, Any]] = []
    if act_required:
        for raw_task in directive.get("actor_tasks", []):
            if not isinstance(raw_task, dict):
                continue
            target = str(raw_task.get("target_agent_id", ""))
            visible_facts = tuple(
                contracts.FactRef(
                    fact_id=str(
                        info.get("fact_id", info)
                        if isinstance(info, dict)
                        else info
                    ),
                    text=str(
                        info.get("text", info)
                        if isinstance(info, dict)
                        else info
                    ),
                    visibility=str(
                        info.get("visibility", "public")
                        if isinstance(info, dict)
                        else "public"
                    ),
                )
                for info in raw_task.get(
                    "visible_facts", raw_task.get("information_ids", [])
                )
            )
            try:
                task = contracts.AgentTask(
                    task_id=str(raw_task.get("task_id", "")),
                    target_agent_id=target,
                    objective=str(raw_task.get("objective", "")),
                    source_reference=str(
                        raw_task.get("source_reference")
                        or bi.get("current_beat_id", "")
                    ),
                    visible_facts=visible_facts,
                    allowed_actions=tuple(
                        raw_task.get("allowed_actions", ("speak", "act"))
                    ),
                    constraints=tuple(raw_task.get("constraints", ())),
                    success_condition=str(
                        raw_task.get(
                            "success_condition",
                            "产生符合角色的行动或明确不行动原因",
                        )
                    ),
                )
            except ValueError:
                continue
            if target in {"yangjian", "杨戬"}:
                perception_text = state_manager.get_perception(
                    "yangjian", state, ""
                )
                perception = tuple(
                    (
                        contracts.FactRef(
                            fact_id="room_perception",
                            text=perception_text,
                            visibility="private",
                        ),
                        *visible_facts,
                    )
                    if perception_text.strip()
                    else visible_facts
                )
                actor_ref = contracts.AgentRef(
                    agent_id="yangjian", kind=contracts.AgentKind.ACTOR
                )
                actor_request = contracts.new_message(
                    turn_id=turn_id,
                    story_id=str(bi.get("story_id", "story_1")),
                    beat_id=str(bi.get("current_beat_id", "")),
                    phase=contracts.Phase.ACT,
                    sender=room_ref,
                    recipient=actor_ref,
                    message_type="yangjian.turn.input",
                    correlation_id=direct_response.message_id,
                    payload=contracts.YangJianTurnInput(
                        task=task,
                        scene={"id": bi.get("current_beat_id", "")},
                        public_room_history=public_history_tuple,
                        perception=perception,
                    ),
                )
                result = contracts.to_dict(
                    yangjian.handle_message(actor_request).payload
                )
            else:
                turn_input = npc.build_structured_turn_input(
                    target,
                    task=task,
                    scene={"id": bi.get("current_beat_id", "")},
                    public_room_history=public_history_tuple,
                    perception=visible_facts,
                )
                actor_request = contracts.new_message(
                    turn_id=turn_id,
                    story_id=str(bi.get("story_id", "story_1")),
                    beat_id=str(bi.get("current_beat_id", "")),
                    phase=contracts.Phase.ACT,
                    sender=room_ref,
                    recipient=contracts.AgentRef(
                        agent_id=target, kind=contracts.AgentKind.ACTOR
                    ),
                    message_type="npc.turn.input",
                    correlation_id=direct_response.message_id,
                    payload=turn_input,
                )
                result = contracts.to_dict(
                    npc.handle_agent_message(actor_request).payload
                )
            actor_results.append(result)

    forbidden_fragments = list(bi.get("forbidden_reveals", []))
    for task in directive.get("actor_tasks", []):
        forbidden_fragments.extend(task.get("constraints", []))

    resolve_response = None
    if resolve_required:
        resolve_request = contracts.new_message(
            turn_id=turn_id,
            story_id=str(bi.get("story_id", "story_1")),
            beat_id=str(bi.get("current_beat_id", "")),
            phase=contracts.Phase.RESOLVE,
            sender=room_ref,
            recipient=director_ref,
            message_type="director.resolve.input",
            correlation_id=direct_response.message_id,
            payload=contracts.DirectorResolveInput(
                directive_id=str(directive["directive_id"]),
                story_cursor={
                    "story_id": bi.get("story_id", "story_1"),
                    "beat_id": bi.get("current_beat_id", ""),
                    "allowed_information": list(
                        bi.get("allowed_information", [])
                    ),
                },
                world_snapshot=dict(state),
                actor_results=tuple(
                    contracts.actor_turn_result_from_dict(item)
                    for item in actor_results
                ),
                user_event={
                    "type": "user_message",
                    "text": user_message or "",
                },
                user_turn=dict(directive.get("user_turn", {})),
                unlocked_transitions=tuple(
                    bi.get("available_transitions", ())
                ),
                allowed_state_operations=(
                    "set_world_attribute",
                    "move_item",
                    "reveal_fact",
                    "set_character_state",
                    "advance_beat",
                ),
            ),
        )
        resolve_response = director.handle_resolve(resolve_request)
        resolution = contracts.to_dict(resolve_response.payload)
        resolution["state_changes"] = resolution.pop("state_operations", [])
        resolution["next_beat"] = resolution.pop("next_beat_id", None)
        for decision in resolution.get("decisions", []):
            if "result_id" in decision:
                decision["proposal_id"] = decision.pop("result_id")
        outputs, confirmed_events = _apply_actor_resolution(
            actor_results, resolution, forbidden_fragments
        )
        confirmed_events.extend(
            _confirmed_events_from_user_outcome(
                resolution.get("user_outcome")
            )
        )
    else:
        inline_effects = directive.get("inline_effects") or {}
        resolution = {
            "state_changes": list(inline_effects.get("state_operations", [])),
            "next_beat": None,
            "continuation": {
                "kind": "continue_current",
                "reason": str(
                    resolve_gate.get("reason", "fast_path_no_resolve")
                ),
            },
            "decisions": [],
            "user_outcome": None,
        }
        outputs, confirmed_events = _auto_accept_actor_results(
            actor_results, forbidden_fragments
        )
        confirmed_events.extend(
            _confirmed_events_from_user_feedback(
                inline_effects.get("user_feedback")
            )
        )

    result_by_id = {
        item.get("result_id"): item for item in actor_results
    }
    for decision in resolution.get("decisions", []):
        if decision.get("result") not in {"accept", "modify"}:
            continue
        actor_result = result_by_id.get(decision.get("proposal_id"))
        if actor_result and actor_result.get("agent_id") not in {
            "yangjian",
            "杨戬",
        }:
            npc.record_accepted(
                actor_result["agent_id"],
                event_id=decision.get("proposal_id", ""),
                summary=str(decision.get("outcome_summary", "")),
            )

    narration_spec = _select_narration_spec(directive, resolution)
    if narration_spec:
        narration_events = list(confirmed_events)
        if not narration_events:
            narration_events = _synthetic_confirmed_events_for_narration(
                narration_spec
            )
        request = contracts.NarrationRequest(
            purpose=str(narration_spec.get("purpose", "visible_action")),
            timing=str(narration_spec.get("timing", "after_dialogue")),
            visible_fact_ids=tuple(
                narration_spec.get("visible_fact_ids", ())
            ),
            max_characters=int(narration_spec.get("max_characters", 100)),
            style_profile=str(
                narration_spec.get("style_profile", "concise")
            ),
            brief=str(narration_spec.get("brief", "")),
            scene_facts=tuple(narration_spec.get("scene_facts", ())),
        )
        visible_facts = tuple(
            contracts.FactRef(fact_id=fact_id, text=fact_id)
            for fact_id in request.visible_fact_ids
        )
        narrator_request_message = contracts.new_message(
            turn_id=turn_id,
            story_id=str(bi.get("story_id", "story_1")),
            beat_id=str(bi.get("current_beat_id", "")),
            phase=contracts.Phase.NARRATE,
            sender=room_ref,
            recipient=contracts.AgentRef(
                agent_id="narrator",
                kind=contracts.AgentKind.NARRATOR,
            ),
            message_type="narrator.input",
            correlation_id=(
                resolve_response.message_id
                if resolve_response is not None
                else direct_response.message_id
            ),
            payload=contracts.NarratorInput(
                narration_request=request,
                scene={"id": bi.get("current_beat_id", "")},
                confirmed_events=tuple(narration_events),
                visible_facts=visible_facts,
                previous_published_messages=public_history_tuple,
            ),
        )
        draft = contracts.to_dict(
            narrator.handle_message(narrator_request_message).payload
        )
        narration = str(draft.get("text", ""))
        if (
            narration
            and not draft.get("contains_dialogue")
            and not _contains_forbidden(narration, forbidden_fragments)
        ):
            narration_output = {
                "role": "旁白",
                "text": narration,
                "kind": "narration",
            }
            if request.timing == "before_dialogue":
                outputs.insert(0, narration_output)
            else:
                outputs.append(narration_output)

    order = [output["role"] for output in outputs] + ["用户"]

    # Phase 4: Room 保存 + 事实管理
    state["tick"] = state.get("tick", 0) + 1
    if user_message:
        state.setdefault("event_log", []).append(f"[tick{state['tick']}] 用户: {user_message[:120]}")
        contracts.append_published_message(
            state,
            turn_id=turn_id,
            role="用户",
            kind="dialogue",
            text=user_message,
        )
    for o in outputs:
        if o["text"]:
            state.setdefault("event_log", []).append(f"[tick{state['tick']}] {o['role']}: {o['text'][:200]}")
            contracts.append_published_message(
                state,
                turn_id=turn_id,
                role=o["role"],
                kind=o.get("kind", "dialogue"),
                text=o["text"],
                confirmed_event_ids=tuple(o.get("confirmed_event_ids", ())),
            )

    # 更新公共事实
    import story_facts as sf
    directive_scene = directive.get("current_beat", "")
    if directive_scene:
        f = sf.load_facts()
        f["current_scene"] = directive_scene
        sf.save_facts(f)

    # 应用 resolution 中的状态变更
    for change in resolution.get("state_changes", []):
        key = change.get("key", "")
        value = change.get("value")
        if key and value is not None:
            if key in {"weather", "mood", "world_day"}:
                state = state_manager.apply_changes(state, {key: value})
            from langfuse_logger import log_state_change
            log_state_change(lf_ctx, key, value, source="resolve")
            if key.startswith("item_"):
                item = key.replace("item_", "")
                sf.set_item_location(item, str(value))
            elif key.startswith("reveal_"):
                sf.reveal_information(str(value))
            elif key.startswith("character_"):
                character = key.replace("character_", "")
                sf.set_character_state(character, str(value))

    # 推进 beat（若有）
    next_beat = resolution.get("next_beat")
    unlocked_next_beats = {
        item.get("target_id") for item in bi.get("available_transitions", [])
    }
    if next_beat and next_beat in unlocked_next_beats:
        ss.advance_beat(ss_state, next_beat)
        # 更新 director 缓存
        bi = ss.get_current_beat_info(ss.load_state())
        director.set_story_context(bi)
        # 检查副线解锁
        ss.check_and_unlock_side_arcs(ss.load_state())

    # 偏离检测
    deviation_signal = directive.get("observed_user_intent", {}).get("intent", "")
    if deviation_signal and deviation_signal not in ("continue", "engage"):
        needs = ss.record_deviation(ss_state, user_message or "")
        if needs and not ss_state.get("in_recovery"):
            _trigger_recovery(ss_state, user_message or "", directive)
    else:
        ss.clear_deviation(ss_state)

    # 限制日志长度
    if len(state.get("event_log", [])) > 500:
        state["event_log"] = state["event_log"][-500:]

    state_manager.save(state)

    # Langfuse flush（不阻塞主流程）
    try:
        lf_flush(lf_ctx)
    except Exception:
        pass

    decision_out = {
        "scene": directive.get("current_beat", ""),
        "order": order,
        "outcome": resolution.get("next_beat", ""),
    }

    room_llm.clear_trace_context()
    return {
        "ok": True,
        "output": outputs,
        "state": state,
        "decision": decision_out,
        "directive": directive,
        "resolution": resolution,
        "actor_results": actor_results,
        "npc_command_results": npc_command_results,
    }


def _contains_forbidden(text: str, forbidden: list[str]) -> bool:
    normalized = text.casefold()
    for item in forbidden:
        fragment = str(item).strip().casefold()
        for prefix in ("不能透露", "禁止透露", "不得透露", "不能", "禁止", "不得"):
            if fragment.startswith(prefix):
                fragment = fragment[len(prefix):].strip(" ：:")
                break
        if fragment and fragment in normalized:
            return True
    return False


def _auto_accept_actor_results(
    actor_results: list[dict[str, Any]],
    forbidden: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fast path: accept actor proposals without a RESOLVE LLM call."""
    decisions = []
    for actor_result in actor_results:
        result_id = str(actor_result.get("result_id", ""))
        if actor_result.get("kind") == "abstain":
            decisions.append({
                "proposal_id": result_id,
                "result": "accept_abstention",
                "outcome_summary": "角色本回合暂不行动",
            })
            continue
        proposal = actor_result.get("proposal") or {}
        decisions.append({
            "proposal_id": result_id,
            "result": "accept",
            "outcome_summary": "角色行动被采纳",
            "final_dialogue": proposal.get("dialogue"),
            "final_action": proposal.get("action"),
        })
    return _apply_actor_resolution(
        actor_results,
        {
            "decisions": decisions,
            "continuation": {
                "kind": "continue_current",
                "reason": "fast path auto-accept",
            },
        },
        forbidden,
    )


def _confirmed_events_from_user_feedback(
    user_feedback: Any,
) -> list[dict[str, Any]]:
    if not isinstance(user_feedback, Mapping):
        return []
    summary = str(user_feedback.get("outcome_summary", "")).strip()
    if not summary:
        return []
    return [{
        "event_id": "confirmed_user_feedback",
        "event_type": "user_action",
        "summary": summary,
        "participants": ["user"],
        "fact_ids": list(user_feedback.get("revealed_fact_ids", [])),
    }]


def _confirmed_events_from_user_outcome(
    user_outcome: Any,
) -> list[dict[str, Any]]:
    if not isinstance(user_outcome, Mapping):
        return []
    if user_outcome.get("applies") is not True:
        return []
    summary = str(user_outcome.get("outcome_summary", "")).strip()
    if not summary:
        return []
    return [{
        "event_id": "confirmed_user_outcome",
        "event_type": "user_action",
        "summary": summary,
        "participants": ["user"],
        "fact_ids": list(user_outcome.get("revealed_fact_ids", [])),
    }]


def _synthetic_confirmed_events_for_narration(
    narration_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    brief = str(narration_spec.get("brief", "")).strip()
    if brief:
        events.append({
            "event_id": "confirmed_director_narration_brief",
            "event_type": "scene_anchor",
            "summary": brief,
            "participants": [],
            "fact_ids": [],
        })
    for index, fact in enumerate(narration_spec.get("scene_facts", ())):
        text = str(fact).strip()
        if not text:
            continue
        events.append({
            "event_id": f"confirmed_director_scene_fact_{index + 1}",
            "event_type": "scene_fact",
            "summary": text,
            "participants": [],
            "fact_ids": [],
        })
    return events


def _select_narration_spec(
    directive: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any] | None:
    inline_feedback = (directive.get("inline_effects") or {}).get("user_feedback")
    if isinstance(inline_feedback, Mapping):
        presentation = inline_feedback.get("presentation")
        if isinstance(presentation, Mapping) and presentation.get("required"):
            return {
                "purpose": presentation.get("purpose", "visible_action"),
                "timing": presentation.get("timing", "after_dialogue"),
                "visible_fact_ids": list(
                    inline_feedback.get("revealed_fact_ids", [])
                ),
                "max_characters": 100,
                "style_profile": "concise",
            }

    user_outcome = resolution.get("user_outcome")
    if isinstance(user_outcome, Mapping):
        presentation = user_outcome.get("presentation")
        if isinstance(presentation, Mapping) and presentation.get("required"):
            return {
                "purpose": presentation.get("purpose", "visible_action"),
                "timing": presentation.get("timing", "after_dialogue"),
                "visible_fact_ids": list(
                    user_outcome.get("revealed_fact_ids", [])
                ),
                "max_characters": 100,
                "style_profile": "concise",
            }

    narration_request = directive.get("narration_request")
    if isinstance(narration_request, Mapping):
        return {
            "purpose": narration_request.get("purpose", "visible_action"),
            "timing": narration_request.get("timing", "after_dialogue"),
            "visible_fact_ids": list(
                narration_request.get("visible_fact_ids", [])
            ),
            "max_characters": narration_request.get("max_characters", 100),
            "style_profile": narration_request.get("style_profile", "concise"),
            "brief": narration_request.get("brief", ""),
            "scene_facts": list(narration_request.get("scene_facts", [])),
        }
    return None


def _apply_actor_resolution(
    actor_results: list[dict[str, Any]],
    resolution: dict[str, Any],
    forbidden: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Publish only Director-adjudicated structured actor content."""
    result_by_id = {
        str(item.get("result_id", "")): item for item in actor_results
    }
    outputs: list[dict[str, Any]] = []
    confirmed_events: list[dict[str, Any]] = []
    for index, decision in enumerate(resolution.get("decisions", [])):
        result_id = str(decision.get("proposal_id", ""))
        actor_result = result_by_id.get(result_id)
        if actor_result is None:
            continue
        decision_result = str(decision.get("result", "reject"))
        if decision_result in {"reject", "accept_abstention"}:
            continue
        proposal = actor_result.get("proposal")
        if not isinstance(proposal, dict):
            continue
        if decision_result == "modify":
            dialogue = decision.get("final_dialogue")
            action = decision.get("final_action")
        else:
            dialogue = decision.get("final_dialogue", proposal.get("dialogue"))
            action = decision.get("final_action", proposal.get("action"))
        agent_id = str(actor_result.get("agent_id", ""))
        role = "杨戬" if agent_id in {"yangjian", "杨戬"} else agent_id
        outcome = str(decision.get("outcome_summary", "")).strip()
        event_id = f"confirmed_{result_id or index + 1}"
        if outcome:
            confirmed_events.append({
                "event_id": event_id,
                "event_type": "actor_result",
                "summary": outcome,
                "participants": [agent_id],
                "fact_ids": [],
            })
        if isinstance(action, dict):
            text = str(action.get("description", "")).strip()
            if text and not _contains_forbidden(text, forbidden):
                outputs.append({
                    "role": f"{role}的动作",
                    "kind": "action",
                    "text": text,
                    "npc_id": (
                        None if agent_id in {"yangjian", "杨戬"} else agent_id
                    ),
                    "confirmed_event_ids": [event_id] if outcome else [],
                })
        if isinstance(dialogue, dict):
            text = str(dialogue.get("text", "")).strip()
            if text and not _contains_forbidden(text, forbidden):
                outputs.append({
                    "role": role,
                    "kind": "dialogue",
                    "text": text,
                    "npc_id": (
                        None if agent_id in {"yangjian", "杨戬"} else agent_id
                    ),
                    "confirmed_event_ids": [event_id] if outcome else [],
                })
    continuation = resolution.get("continuation", {})
    if (
        not confirmed_events
        and isinstance(continuation, dict)
        and continuation.get("kind") == "world_event"
        and isinstance(continuation.get("world_event"), dict)
    ):
        world_event = continuation["world_event"]
        confirmed_events.append({
            "event_id": "confirmed_director_world_event",
            "event_type": str(world_event.get("event_type", "world_event")),
            "summary": str(world_event.get("summary", "")),
            "participants": [],
            "fact_ids": [],
        })
    return outputs, confirmed_events


def _apply_resolution(
    proposals: list[dict[str, Any]],
    resolution: dict[str, Any],
    forbidden: list[str],
) -> tuple[list[dict[str, str]], list[str]]:
    proposal_by_id = {p["proposal_id"]: p for p in proposals}
    outputs: list[dict[str, str]] = []
    outcomes: list[str] = []
    for decision in resolution.get("decisions", []):
        proposal = proposal_by_id.get(decision.get("proposal_id"))
        if not proposal or decision.get("result") == "reject":
            continue
        outcome = str(decision.get("outcome_summary", "")).strip()
        if outcome:
            outcomes.append(outcome)
        text = proposal["text"]
        if (
            decision.get("result") == "modify"
            and proposal["kind"] == "action"
            and outcome
        ):
            text = outcome
        if not _contains_forbidden(text, forbidden):
            outputs.append({"role": proposal["role"], "text": text})
    return outputs, outcomes


def _capture_explicit_preferences(message: str, user_id: str) -> None:
    """Record only unambiguous user feedback; never infer hidden preferences."""
    dimensions = {
        "搞笑": "tone.humor",
        "幽默": "tone.humor",
        "戏剧": "tone.drama",
        "冲突": "intensity.conflict",
        "节奏快": "pacing.fast",
        "节奏慢": "pacing.slow",
        "少描写": "style.concise_narration",
        "简洁": "style.concise_narration",
    }
    positive = ("我喜欢", "我想要", "希望多", "多一点")
    negative = ("我不喜欢", "不要", "希望少", "少一点", "太多")
    if not any(marker in message for marker in (*positive, *negative)):
        return
    try:
        from yangjian_story_generator.preference_store import (
            DEFAULT_STORE_PATH,
            PreferenceStore,
        )
        store = PreferenceStore(
            runtime_context.scoped_path(DEFAULT_STORE_PATH),
            user_id=user_id,
        )
        direction = (
            "decrease"
            if any(marker in message for marker in negative)
            else "increase"
        )
        for keyword, dimension in dimensions.items():
            if keyword in message:
                store.record_feedback(
                    dimension=dimension,
                    direction=direction,
                    evidence_summary=f"用户明确反馈：{message[:120]}",
                )
    except Exception:
        pass


def _tick_traditional_fallback(state, user_message=None, source="cron"):
    """回退到传统单次 tick。"""
    decision = director.decide(state, user_message)
    outputs = []
    for role in decision.get("order", []):
        if role == "旁白":
            text = narrator.speak(decision, state)
            outputs.append({"role": "旁白", "text": text})
        elif role == "杨戬":
            result = yangjian.act(decision, state_manager.get_perception("yangjian", state, decision.get("outcome", "")))
            for a in result.get("actions", []): outputs.append({"role": "杨戬的动作", "text": a})
            for d in result.get("dialogues", []): outputs.append({"role": "杨戬", "text": d})
        elif role.startswith("NPC_"):
            npc_name = role.replace("NPC_", "")
            result = npc.act(npc_name, decision, state_manager.get_perception(npc_name, state, decision.get("outcome", "")))
            for a in result.get("actions", []): outputs.append({"role": f"{npc_name}的动作", "text": a})
            for d in result.get("dialogues", []): outputs.append({"role": npc_name, "text": d})
        elif role == "用户":
            pass
        else:
            outputs.append({"role": role, "text": f"【{role} 没有对应的 Agent】"})

    changes = decision.get("world_changes", {})
    if changes:
        state = state_manager.apply_changes(state, changes)
    for sk, sv in state.get("stories", {}).items():
        if sv.get("triggered") or sv.get("phase", 0) > 0:
            sv["ticks_stalled"] = sv.get("ticks_stalled", 0) + 1
    story_changes = changes.get("stories", {})
    for sk, sv in state.get("stories", {}).items():
        if sk in story_changes and "phase" in story_changes.get(sk, {}):
            sv["ticks_stalled"] = 0

    if user_message:
        state.setdefault("event_log", []).append(f"[tick{state.get('tick',0)}] 用户: {user_message[:120]}")
    for o in outputs:
        if o["text"] and o["role"] not in ("用户", "系统"):
            state.setdefault("event_log", []).append(f"[tick{state.get('tick',0)}] {o['role']}: {o['text'][:200]}")
    director_events = changes.get("public_event_log", changes.get("event_log", []))
    if director_events:
        for e in director_events:
            state.setdefault("event_log", []).append(f"[tick{state.get('tick',0)}] 事件: {e[:200]}")
    if len(state.get("event_log", [])) > 500:
        state["event_log"] = state["event_log"][-500:]

    if _story_plan_active and user_message:
        import story_state as ss
        ss_state = ss.load_state()
        if ss_state.get("status") == "active":
            deviation = decision.get("deviation_signal")
            if deviation:
                needs = ss.record_deviation(ss_state, user_message)
                if needs and not ss_state.get("in_recovery"):
                    compatible = decision.get("deviation_compatible", False)
                    if not compatible:
                        _trigger_recovery(ss_state, user_message, decision)
            else:
                ss.clear_deviation(ss_state)

    state["tick"] = state.get("tick", 0) + 1
    state_manager.save(state)
    return {"ok": True, "output": outputs, "state": state, "decision": decision}


# ── 格式化输出 ──────────────────────────────────────────────


def format_output(result):
    """将 tick 结果格式化为可发送的故事文本"""
    state = result.get("state", {})
    if not result.get("ok"):
        lines = []
        for item in result.get("output", []):
            lines.append(item.get("text", ""))
        return "\n".join(lines)
    
    lines = []
    for item in result.get("output", []):
        role = item.get("role", "")
        text = item.get("text", "")
        
        if role == "杨戬的动作":
            lines.append(f"【杨戬的动作】{text}")
        elif role == "杨戬":
            lines.append(f"【杨戬】{text}")
        elif role.endswith("的动作"):
            lines.append(f"【{role}】{text}")
        elif role in state.get("npc", {}) or role.startswith("NPC_"):
            lines.append(f"【{role}】{text}")
        elif role == "旁白":
            lines.append(f"【旁白】{text}")
        elif role == "织梦者":
            lines.append(f"【织梦者】{text}")
        elif role == "系统":
            lines.append(text)
        else:
            lines.append(f"【{role}】{text}")
    
    return "\n".join(lines)


def print_tick(user_message=None, source="cron"):
    """运行 tick 并打印结果"""
    result = tick(user_message, source)
    story = format_output(result)
    
    print("=" * 50)
    print(f"【Room Tick】 触发源: {source}")
    if _story_plan_active:
        import story_state as ss
        ss_state = ss.load_state()
        if ss_state.get("status") == "active":
            bi = ss.get_current_beat_info(ss_state)
            print(f"Beat: {bi.get('current_beat_id', '?')} — {bi.get('beat_purpose', '?')[:60]}")
    if result.get("ok"):
        print(f"场景: {result['decision'].get('scene', '?')}")
        print(f"排场: {' → '.join(result['decision'].get('order', []))}")
    else:
        print(f"错误: {result.get('error', '?')}")
    print("=" * 50)
    
    if user_message:
        print(f"\n💬【你的消息】{user_message}\n")
        print("-" * 30)
    
    print(story)
    print("\n" + "=" * 50)
    
    return result


if __name__ == "__main__":
    user_msg = sys.argv[1] if len(sys.argv) > 1 else None
    source = sys.argv[2] if len(sys.argv) > 2 else "cron"
    
    # 检查是否有 --story 参数
    if "--story" in sys.argv:
        activate_story_plan()
        print(f"故事计划已激活")
    
    print_tick(user_msg, source)
