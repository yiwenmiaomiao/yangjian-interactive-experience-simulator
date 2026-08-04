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
import time
import traceback
import threading
from functools import wraps
from typing import Any, Mapping

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_context, state_manager, story_engine
from agent_ids import display_agent_name, is_yangjian, normalize_agent_id
from langfuse_logger import (
    LangfuseCtx,
    flush as lf_flush,
    log_error,
    log_event,
    log_state_change,
    room_phase,
    start_room_trace,
    end_room_trace,
)
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
            return function(*args, **kwargs)
    return wrapped


def _acquire_room_process_lock(lf_ctx: LangfuseCtx):
    """Process lock after the main Room trace is open so lock spans nest."""
    import time as _time

    lock_path = os.path.join(PROFILE_DIR, ".room_tick.lock")
    wait_started = _time.monotonic()
    cm = runtime_context.process_lock(lock_path)
    cm.__enter__()
    waited = _time.monotonic() - wait_started
    if waited > 0.2:
        try:
            log_event(
                lf_ctx,
                "room.lock_waited",
                output_data={"waited_s": round(waited, 3)},
                level="WARNING",
            )
        except Exception:
            pass
    return cm


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
        "beat_info": beat.get("beat_plot", "") if not beat.get("error") else beat["error"],
        "completed_beats": len(state.get("completed_beats", [])),
        "flags": state.get("flags", {}),
        "in_recovery": state.get("in_recovery", False),
    }, ensure_ascii=False)


# ── 回归剧情生成 ──────────────────────────────────────────



def _trigger_recovery(ss_state: dict[str, Any], user_message: str, decision: dict[str, Any]) -> None:
    """beat 到达最大轮次但 goal 未达成时，生成短回归弧。"""
    import story_state as ss

    plan = ss.get_plan()
    if not plan:
        return

    current_beat_id = ss_state.get("current_beat_id", "")
    current_beat_plot = ""
    for beat in plan.main_arc.beats:
        if beat.beat_id == current_beat_id:
            current_beat_plot = beat.plot
            break

    # 找下个主线 beat 作为回归目标
    rejoin_target = ""
    _is_last_beat = False
    for i, beat in enumerate(plan.main_arc.beats):
        if beat.beat_id == current_beat_id:
            if i + 1 < len(plan.main_arc.beats):
                rejoin_target = plan.main_arc.beats[i + 1].beat_id
            else:
                _is_last_beat = True
            break

    # Bug4 修复：如果当前 beat 是最后一个主线 beat，不进 recovery，
    # 直接推进到结局（如果有结局可选）或标记故事完成
    if _is_last_beat or not rejoin_target:
        endings = [e.ending_id for e in plan.main_arc.endings]
        if endings:
            # 推进到第一个可用结局
            rejoin_target = endings[0]
            ss.advance_beat(ss_state, rejoin_target)
            bi = ss.get_current_beat_info(ss_state)
            director.set_story_context(bi)
            log_event(
                LangfuseCtx(),
                "room.last_beat_force_ending",
                output_data={
                    "beat_id": current_beat_id,
                    "ending_id": rejoin_target,
                },
                level="WARNING",
            )
            return
        else:
            # 没有结局定义，标记故事完成
            ss_state["status"] = "completed"
            ss.save_state(ss_state)
            log_event(
                LangfuseCtx(),
                "room.story_completed_no_ending",
                output_data={"beat_id": current_beat_id},
                level="WARNING",
            )
            return

    # 不再 fallback 到 beats[0]

    recovery_id = f"recovery_{current_beat_id}"

    system = (
        "你是一个短回归剧情架构师。\n"
        "用户在当前剧情节点停留过久（超过最大轮次）但未达成节点目标。"
        "你需要生成一个极短的回归弧（1个beat），自然地把用户引向下一个剧情节点。\n\n"
        "## 规则\n"
        "1. 承认用户刚才的行为，不能假装没发生\n"
        "2. 设定一个明确的子目标（sub_goal），用户达成后即可进入下一节点\n"
        "3. 用户不能感觉被强制纠正\n"
        "4. 不要预写对白\n"
        "5. 输出 JSON 格式\n\n"
        "## 输出格式\n"
        "{\n"
        "  \"sub_goal\": \"用户需要达成的子目标（如：接受杨戬暂时不能透露真相）\",\n"
        "  \"beats\": [\n"
        "    {\n"
        "      \"beat_id\": \"r1\",\n"
        "      \"purpose\": \"利用用户当前关注点自然引向下一节点\",\n"
        "      \"participants\": [\"user\", \"yangjian\"],\n"
        "      \"allowed_information\": [],\n"
        "      \"forbidden_information\": [\"main_ending\"],\n"
        "      \"transitions\": [\n"
        "        {\"transition_id\": \"r1_to_rejoin\", \"target_id\": \"" + rejoin_target + "\", \"preserved_consequences\": [\"用户已接受当前局面\"]}\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    user_prompt = (
        f"## 用户最新消息\n{user_message[:200]}\n\n"
        f"## 当前主线 Beat\nID: {current_beat_id}\n"
        f"目的: {current_beat_plot[:200]}\n"
    
        f"## 主线目标\n{plan.main_arc.goal[:200]}\n\n"
        f"## 回归目标 Beat\n{rejoin_target}\n\n"
        f"## 要求\n"
        f"生成 1 个回归 beat，设定子目标，利用用户当前关注点自然引向下一节点。"
    )

    room_dir = os.path.join(PROFILE_DIR, "room")
    if room_dir not in sys.path:
        sys.path.insert(0, room_dir)
    import llm as room_llm
    raw = room_llm.call(
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.7,
        max_tokens=4000,
    )

    try:
        text = raw.strip()
        for prefix in ("```json", "```"):
            if prefix in text:
                text = text.split(prefix, 1)[1]
                text = text.rsplit("```", 1)[0]
                break
        recovery_data = json.loads(text.strip())
        beats_raw = recovery_data.get("beats", [])
        sub_goal = recovery_data.get("sub_goal", "")

        if _valid_recovery_beats(beats_raw, rejoin_target):
            recovery_beats = []
            for b in beats_raw:
                recovery_beats.append({
                    "beat_id": b["beat_id"],
                    "purpose": b["purpose"],
                    "participants": b.get("participants", ["user", "yangjian"]),
                    "allowed_information": b.get("allowed_information", []),
                    "forbidden_information": b.get("forbidden_information", ["main_ending"]),
                    "transitions": b.get("transitions", [{"transition_id": f"{b['beat_id']}_to_rejoin", "target_id": rejoin_target}]),
                })

            ss.enter_recovery_arc(
                ss_state,
                recovery_id,
                recovery_beats,
                rejoin_target,
                sub_goal=sub_goal,
                max_turns=ss.RECOVERY_MAX_TURNS_DEFAULT,
            )
            # recovery 是 beat 内支线，不覆盖 beat_goal/beat_max_turns
            # 更新 beat_info 缓存让导演使用（叠加 recovery 上下文）
            bi = ss.get_current_beat_info(ss_state)
            director.set_story_context(bi)
    except (json.JSONDecodeError, KeyError):
        pass


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


def _generate_recovery_transition(
    bi: dict[str, Any],
    rejoin_target: str,
    user_message: str,
    ss_state: dict[str, Any],
) -> str:
    """recovery 强制退出时，调 narrator 写一段过渡剧情衔接下一 beat。"""
    import story_state as ss

    plan = ss.get_plan()
    if not plan:
        return ""

    # 找到 rejoin_target 的 beat purpose
    target_plot = ""
    target_beat = ss._find_beat(plan, rejoin_target)
    if target_beat:
        target_plot = target_beat.plot

    # 找当前 recovery 的子目标
    sub_goal = ss_state.get("recovery_sub_goal", "")
    current_beat_plot = bi.get("beat_plot", "")

    system = (
        "你是杨戬项目的旁白。用户在剧情缓冲段停留过久，现在需要强行推进到下一个剧情节点。\n"
        "请写一段过渡旁白（100-200字），自然地将当前局面衔接到下一个剧情节点。\n"
        "规则：\n"
        "1. 可以替用户做简短行动（如'你跟着杨戬走出了密室'）\n"
        "2. 时间可以跳跃（如'午后'、'片刻后'）\n"
        "3. 不要写角色对白\n"
        "4. 为下一个剧情节点做铺垫，但不直接透露具体内容\n"
        "5. 用第二人称'你'视角"
    )
    prompt = (
        f"当前剧情节点：{current_beat_plot[:200]}\n"
        f"缓冲段子目标：{sub_goal[:200]}\n"
        f"下一剧情节点：{rejoin_target}\n"
        f"下一节点剧情：{target_plot[:200]}\n"
        f"用户最后的行为：{user_message[:200]}\n\n"
        f"请写一段过渡旁白。"
    )

    room_dir = os.path.join(PROFILE_DIR, "room")
    if room_dir not in sys.path:
        sys.path.insert(0, room_dir)
    import llm as room_llm
    raw = room_llm.call(
        agent_id="narrator",
        system=system,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=500,
    )
    text = str(raw or "").strip()
    if len(text) > 250:
        text = text[:250]
    return text


@_serialized_tick
def tick(
    user_message=None,
    source="cron",
    *,
    user_id: str = "default",
    thread_id: str = "default",
    job_name: str | None = None,
    lf_ctx: Any = None,
):
    """
    执行一个 Room Tick。
    
    Args:
        user_message: 用户输入文本，None 表示定时推动
        source: 触发源 "cron" 或 "user"
        job_name: cron 定时作业名；也可用环境变量 ROOM_CRON_JOB_NAME
    
    Returns:
        dict: {"ok": bool, "output": [...], "state": {...}, "decision": {...}}
    """
    global _story_plan_active

    resolved_job = (
        job_name
        or os.environ.get("ROOM_CRON_JOB_NAME", "")
        or ""
    )
    identity_token = runtime_context.set_identity(user_id, thread_id)
    if lf_ctx is None:
        lf_ctx = LangfuseCtx(
        tick=0,
        user_id=user_id,
        thread_id=thread_id,
        source=source,
        user_message=user_message,
        job_name=resolved_job,
    )
    owns_trace = False
    tick_result: dict[str, Any] | None = None
    process_lock_cm = None
    try:
        owns_trace = (
            start_room_trace(
                lf_ctx,
                name="room.tick",
                input_data={
                    "user_message": user_message,
                    "source": source,
                    "user_id": user_id,
                    "thread_id": thread_id,
                    "job_name": resolved_job,
                },
                only_if_no_parent=True,
            )
            is not None
        )
        try:
            process_lock_cm = _acquire_room_process_lock(lf_ctx)
        except TimeoutError as exc:
            log_error(lf_ctx, "room.lock_timeout", exc)
            tick_result = {
                "ok": False,
                "error": "room_lock_timeout",
                "output": [{
                    "role": "系统",
                    "text": "【Room 忙碌超时，请稍后重试】",
                }],
            }
            return tick_result

        log_event(
            lf_ctx,
            "room.tick_enter",
            input_data={
                "user_message": user_message,
                "source": source,
                "user_id": user_id,
                "thread_id": thread_id,
            },
        )
        with room_phase(lf_ctx, "room.load_state") as phase_bag:
            state = state_manager.load()
            import story_state as ss
            plan = ss.get_plan() or ss.load_plan()
            persisted_story = ss.load_state()
            if isinstance(phase_bag, dict):
                phase_bag["output"] = _story_state_snapshot(
                    persisted_story, state
                )
        lf_ctx.tick = int(state.get("tick", 0)) + 1
        lf_ctx.turn_id = f"turn_{lf_ctx.tick}"
        if user_message:
            _capture_explicit_preferences(user_message, user_id)

        _story_plan_active = bool(
            plan and persisted_story.get("status") in ("active", "completed")
        )
        log_event(
            lf_ctx,
            "room.story_plan_gate",
            output_data={
                "has_plan": bool(plan),
                "story_status": persisted_story.get("status"),
                "current_beat_id": persisted_story.get("current_beat_id"),
                "story_plan_active": _story_plan_active,
            },
            level="DEFAULT" if _story_plan_active else "WARNING",
        )
        if _story_plan_active:
            beat_info = ss.get_current_beat_info(persisted_story)
            director.set_story_context(beat_info)
            lf_ctx.story_id = str(
                beat_info.get("story_id", persisted_story.get("story_id", ""))
            )
            lf_ctx.beat_id = str(beat_info.get("current_beat_id", ""))

        # ── 故事线模式：DIRECT → ACT → RESOLVE ──
        # __STORY_START__ 触发 narrator 开场（story 切换/重置后）
        if user_message == "__STORY_START__":
            result = _tick_story_start(
                source=source, user_id=user_id, thread_id=thread_id, lf_ctx=lf_ctx
            )
            log_event(
                lf_ctx,
                "room.tick_exit",
                output_data={
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                    "output_count": len(result.get("output") or []),
                    "roles": [
                        item.get("role")
                        for item in (result.get("output") or [])
                    ],
                },
            )
            if not owns_trace:
                lf_flush(lf_ctx)
            tick_result = result
            return tick_result
        elif _story_plan_active:
            result = _tick_storyline(
                state, user_message, source, lf_ctx=lf_ctx
            )
            log_event(
                lf_ctx,
                "room.tick_exit",
                output_data={
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                    "output_count": len(result.get("output") or []),
                    "roles": [
                        item.get("role")
                        for item in (result.get("output") or [])
                    ],
                },
            )
            if not owns_trace:
                lf_flush(lf_ctx)
            tick_result = result
            return tick_result

        if os.environ.get("YANGJIAN_ALLOW_LEGACY_MODE") != "1":
            result = {
                "ok": False,
                "error": "story_plan_not_active",
                "output": [{
                    "role": "系统",
                    "text": "【故事计划未激活，已阻止旧版导演直接修改状态】",
                }],
            }
            log_event(
                lf_ctx,
                "room.tick_blocked",
                output_data=result,
                level="ERROR",
            )
            if not owns_trace:
                lf_flush(lf_ctx)
            tick_result = result
            return tick_result

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
        
        # 应用世界变更（scene / world_day 由 story plan beat 定义，Director 不再裁定）
        changes = decision.get("world_changes", {})
        changes = {k: v for k, v in changes.items()
                   if k not in ("weather", "current_weather", "mood", "world_day")}
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

                # beat max_turns 检查：到达最大轮次但 goal 未达成 -> 进入 recovery
                if not ss_state.get("in_recovery") and ss.check_beat_max_turns(ss_state):
                    _trigger_recovery(ss_state, user_message or "", decision)

        
        state["tick"] = state.get("tick", 0) + 1
        
        state_manager.save(state)
        
        tick_result = {
            "ok": True,
            "output": outputs,
            "state": state,
            "decision": decision,
        }
        return tick_result
    
    except Exception as e:
        try:
            import llm as room_llm
            room_llm.clear_trace_context()
        except Exception:
            pass
        log_error(lf_ctx, "room.tick_exception", e, input_data=user_message)
        traceback.print_exc()
        tick_result = {
            "ok": False,
            "error": str(e),
            "output": [{"role": "系统", "text": f"【Room 异常: {e}】"}],
        }
        return tick_result
    finally:
        if process_lock_cm is not None:
            try:
                process_lock_cm.__exit__(None, None, None)
            except Exception:
                pass
        if owns_trace:
            end_room_trace(
                lf_ctx,
                output_data=tick_result,
                level=(
                    "ERROR"
                    if tick_result and not tick_result.get("ok", True)
                    else "DEFAULT"
                ),
                status_message=str(
                    (tick_result or {}).get("error") or "ok"
                ),
            )
        else:
            try:
                lf_flush(lf_ctx)
            except Exception:
                pass
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


def _story_state_snapshot(ss_state: dict[str, Any], world_state: dict[str, Any]) -> dict[str, Any]:
    """全量记录 room 状态快照，用于 trace 开始/结束及 load_state 对比。

    字段按职责分组：beat / recovery，方便排查 tick 前后变化。
    """
    import story_state as ss
    scene = world_state.get("scene", {}) or {}
    rel = dict(ss_state.get("relationship", {}))
    rel.pop("history", None)
    return {
        "tick": world_state.get("tick", 0),
        "world_day": world_state.get("world_day", 1),
        "scene": {
            "location": scene.get("location", ""),
            "weather": scene.get("weather", ""),
            "time_of_day": scene.get("time_of_day", ""),
            "mood": scene.get("mood", ""),
        },
        "beat": {
            "story_id": ss_state.get("story_id", ""),
            "status": ss_state.get("status", ""),
            "current_beat_id": ss_state.get("current_beat_id", ""),
            "beat_tick_counter": ss_state.get("beat_tick_counter", 0),
            "main_progress": ss_state.get("main_progress", 0.0),
            "completed_beats": list(ss_state.get("completed_beats", [])),
            "completed_endings": list(ss_state.get("completed_endings", [])),
            "active_side_arc": ss.get_active_side_arc(ss_state),
        },
        "recovery": {
            "in_recovery": ss_state.get("in_recovery", False),
            "recovery_arc_id": ss_state.get("recovery_arc_id"),
            "recovery_rejoin_target": ss_state.get("recovery_rejoin_target"),
            "recovery_sub_goal": ss_state.get("recovery_sub_goal", ""),
            "recovery_max_turns": ss_state.get("recovery_max_turns", ss.RECOVERY_MAX_TURNS_DEFAULT),
            "recovery_sub_goal_met": ss_state.get("recovery_sub_goal_met", False),
            "recovery_tick_counter": ss_state.get("recovery_tick_counter", 0),
        },
        "relationship": rel,
    }


def _tick_story_start(
    source: str = "cron",
    user_id: str = "default",
    thread_id: str = "default",
    lf_ctx: Any = None,
) -> dict[str, Any]:
    """Story start hook: generate narrator opening for the current beat.

    Triggered when a new story begins (switch or reset).
    Skips director entirely, goes straight to narrator.
    """
    global _story_plan_active
    _story_plan_active = True
    import story_state as ss

    ss_state = ss.load_state()
    if ss_state.get("status") != "active":
        return {"ok": True, "output": []}

    bi = ss.get_current_beat_info(ss_state)
    if bi.get("error"):
        return {"ok": True, "output": []}

    director.set_story_context(bi)

    if lf_ctx is None:
        lf_ctx = LangfuseCtx(
            tick=0, user_id=user_id, thread_id=thread_id,
            source=source, user_message="__STORY_START__",
        )
    # Don't call start_room_trace here - it's called by the caller (tick or photon_bridge)

    narration_spec = {
        "purpose": "scene_opening",
        "timing": "before_dialogue",
        "narration_type": "场景",
        "visible_fact_ids": list(bi.get("allowed_information", [])),
        "max_characters": 200,
        "style_profile": "concise",
        "brief": "故事开始，描述用户此刻看到的环境和氛围",
        "scene_facts": [],
    }

    narration_events = _synthetic_confirmed_events_for_narration(narration_spec, bi)
    request = contracts.NarrationRequest(
        purpose=str(narration_spec.get("purpose", "visible_action")),
        timing=str(narration_spec.get("timing", "after_dialogue")),
        narration_type=str(narration_spec.get("narration_type", "旁白")),
        visible_fact_ids=tuple(narration_spec.get("visible_fact_ids", [])),
        max_characters=int(narration_spec.get("max_characters", 100)),
        style_profile=str(narration_spec.get("style_profile", "concise")),
        brief=str(narration_spec.get("brief", "")),
        scene_facts=tuple(narration_spec.get("scene_facts", [])),
    )

    turn_id = f"story_start_{int(time.time())}"
    room_ref = contracts.AgentRef(agent_id="room", kind=contracts.AgentKind.ROOM)
    visible_facts = tuple(
        contracts.FactRef(fact_id=fact_id, text=fact_id)
        for fact_id in request.visible_fact_ids
    )
    narrator_input = contracts.NarratorInput(
        narration_request=request,
        scene=bi.get("scene", {}),
        confirmed_events=tuple(narration_events),
        visible_facts=visible_facts,
    )
    narrator_msg = contracts.new_message(
        turn_id=turn_id,
        story_id=str(bi.get("story_id", "story_1")),
        beat_id=str(bi.get("current_beat_id", "")),
        phase=contracts.Phase.NARRATE,
        sender=room_ref,
        recipient=contracts.AgentRef(
            agent_id="narrator", kind=contracts.AgentKind.NARRATOR
        ),
        message_type="narrator.input",
        correlation_id="",
        payload=narrator_input,
    )

    narration_output = narrator.handle_message(narrator_msg)

    outputs: list[dict[str, Any]] = []
    draft = narration_output.payload
    if draft.text.strip():
        outputs.append({"role": "旁白", "text": draft.text.strip()})

    return {"ok": True, "output": outputs}


def _tick_storyline(state, user_message=None, source="cron", lf_ctx=None):
    """故事线模式 tick：DIRECT -> ACT -> RESOLVE。"""
    import story_state as ss

    _beat_advanced = False  # 标记 beat 是否在本回合推进

    # Phase 0: 更新 beat_info 缓存
    ss_state = ss.load_state()
    if ss_state.get("status") != "active":
        log_event(
            lf_ctx or LangfuseCtx(),
            "room.storyline_inactive",
            output_data={"status": ss_state.get("status")},
            level="WARNING",
        )
        # 故事线已完成或未激活，进入自由聊天模式
        return _tick_free_chat(state, user_message, source, lf_ctx)

    # ── trace 开始：记录 story state + recovery 全量快照 ──
    log_event(
        lf_ctx or LangfuseCtx(),
        "room.trace_start",
        output_data=_story_state_snapshot(ss_state, state),
    )

    # 初始化 Langfuse 日志上下文
    if lf_ctx is None:
        lf_ctx = LangfuseCtx(
            tick=state.get("tick", 0) + 1,
            story_id=ss_state.get("story_id", ss_state.get("current_beat_id", "story_1")),
            beat_id=ss_state.get("current_beat_id", ""),
            source=source,
        )
    else:
        lf_ctx.tick = state.get("tick", 0) + 1
        lf_ctx.story_id = str(
            ss_state.get("story_id", ss_state.get("current_beat_id", "story_1"))
        )
        lf_ctx.beat_id = str(ss_state.get("current_beat_id", ""))
        lf_ctx.source = source
        lf_ctx.turn_id = f"turn_{lf_ctx.tick}"
    import llm as room_llm
    room_llm.set_trace_context(lf_ctx)

    # 刷新导演上下文。Story Generator 的完整 NPC Profile 只交给
    # NPC Manager；Director 只能看到可引用的 profile_id。
    bi = ss.get_current_beat_info(ss_state)

    # 从 story plan beat 读取场景状态并更新 world_state
    _scene = state.setdefault("scene", {})
    for _field in ("location", "weather", "time_of_day", "mood"):
        _val = bi.get(_field, "")
        if _val:
            _scene[_field] = _val
    _wd = bi.get("world_day", "")
    if _wd:
        state["world_day"] = _wd
    state_manager.save(state)

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
    bi["scene"] = state.get("scene", {})
    # 注入 story_facts 摘要让所有 agent 可见
    import story_facts as sf
    bi["facts_summary"] = sf.get_facts_summary()

    bi["npc_profiles"] = [
        {
            "profile_id": profile.profile_id,
            "requirement_id": profile.requirement_id,
        }
        for profile in profile_specs.values()
    ]
    director.set_story_context(bi)
    log_event(
        lf_ctx,
        "room.phase0_context",
        output_data={
            "beat_id": bi.get("current_beat_id"),
            "active_npcs": active_npcs,
            "npc_profiles": bi.get("npc_profiles"),
            "allowed_information_count": len(bi.get("allowed_information", [])),
            "user_message": user_message,
            "source": source,
        },
    )
    # room.status：当前状态机全量参数快照
    scene = state.get("scene", {})
    log_event(
        lf_ctx,
        "room.status",
        output_data={
            "beat_id": bi.get("current_beat_id"),
            "beat_purpose": bi.get("beat_plot", ""),
            "beat_tick_counter": bi.get("beat_tick_counter", 0),
            "in_recovery": ss_state.get("in_recovery", False),
            "recovery_arc_id": ss_state.get("recovery_arc_id"),
            "recovery_rejoin_target": ss_state.get("recovery_rejoin_target"),
            "recovery_sub_goal": ss_state.get("recovery_sub_goal", ""),
            "recovery_tick_counter": ss_state.get("recovery_tick_counter", 0),
            "recovery_max_turns": ss_state.get("recovery_max_turns", ss.RECOVERY_MAX_TURNS_DEFAULT),
            "completed_beats": ss_state.get("completed_beats", []),
            "main_progress": ss_state.get("main_progress", 0.0),
            "scene_location": scene.get("location", ""),
            "scene_weather": scene.get("weather", ""),
            "scene_time_of_day": scene.get("time_of_day", ""),
            "scene_mood": scene.get("mood", ""),
            "allowed_information": bi.get("allowed_information", []),
            "forbidden_information": bi.get("forbidden_information", []),
            "available_transitions": [
                {"target_id": t.get("target_id"), "consequences": t.get("preserved_consequences")}
                for t in bi.get("available_transitions", [])
            ],
            "relationship": ss_state.get("relationship", {}),
        },
    )

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
                "plot": bi.get("beat_plot", ""),
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
            recent_confirmed_events=tuple(
                state.get("confirmed_events", ())[-20:]
            ),
            liveness={
                "beat_tick_counter": bi.get("beat_tick_counter", 0),
            },
        ),
    )
    with room_phase(
        lf_ctx,
        "room.phase1_direct",
        input_data={"user_message": user_message, "beat_id": bi.get("current_beat_id")},
    ):
        direct_response = director.handle_direct(direct_request)
    directive = contracts.to_dict(direct_response.payload)
    directive["current_story_id"] = bi.get("story_id", "story_1")
    directive["current_beat"] = bi.get("current_beat_id", "")
    log_event(
        lf_ctx,
        "room.directive",
        output_data={
            "directive_id": directive.get("directive_id"),
            "user_turn": directive.get("user_turn"),
            "resolve_gate": directive.get("resolve_gate"),
            "actor_task_targets": [
                task.get("target_agent_id")
                for task in directive.get("actor_tasks", [])
                if isinstance(task, dict)
            ],
            "narration_request": bool(directive.get("narration_request")),
            "desired_progress": directive.get("desired_progress"),
        },
    )

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
                arc_id=(
                    requirement.arc_id if requirement is not None else ""
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

    # ── Bug2 修复：room 判定 beat_tick_counter 达到 max_turns 时强制走 RESOLVE ──
    _beat_max = ss.BEAT_MAX_TURNS_DEFAULT
    _beat_tick = ss_state.get("beat_tick_counter", 0)
    if _beat_max > 0 and _beat_tick >= _beat_max and not resolve_required:
        resolve_required = True
        log_event(
            lf_ctx,
            "room.force_resolve_max_turns",
            output_data={
                "beat_id": bi.get("current_beat_id"),
                "tick_counter": _beat_tick,
                "max_turns": _beat_max,
            },
            level="WARNING",
        )

    # ═══ Phase 2a: Narration 优先 ═══
    # 有 narration 需求时先执行 narrator（旁白揭示环境），输出后再执行 actor。
    # 旁白是用户的眼睛——用户行动后先看到环境，再看到角色反应。
    narration_outputs: list[dict[str, Any]] = []
    narration_spec = _select_narration_spec(directive, resolution=None)
    # 场景变化强制旁白：用户执行物理行动（进入新地点、触碰物体等）时，
    # 即使导演没设 narration.required，也必须触发旁白描述新环境。
    if narration_spec is None:
        user_turn = directive.get("user_turn") or {}
        if user_turn.get("kind") == "physical_action":
            narration_spec = {
                "purpose": "scene_opening",
                "timing": "before_dialogue",
                "narration_type": "场景",
                "visible_fact_ids": list(bi.get("allowed_information", [])),
                "max_characters": 150,
                "style_profile": "concise",
                "brief": "用户执行了物理行动，描述用户此刻看到的新环境",
                "scene_facts": [],
            }
            log_event(
                lf_ctx,
                "room.narration_forced_physical_action",
                output_data={"user_turn_kind": "physical_action"},
                level="WARNING",
            )
    # beat 切换后自动触发场景旁白（_beat_advanced 在后面才设置，这里先检查
    # directive 是否携带场景切换标记；实际 beat 切换强制在 resolve 后处理）
    if narration_spec is None and bi.get("_scene_changed"):
        narration_spec = {
            "purpose": "scene_opening",
            "timing": "before_dialogue",
            "narration_type": "场景",
            "visible_fact_ids": list(bi.get("allowed_information", [])),
            "max_characters": 150,
            "style_profile": "concise",
            "brief": "场景已切换，描述用户此刻看到的新环境",
            "scene_facts": [],
        }
        log_event(
            lf_ctx,
            "room.narration_forced_beat_change",
            output_data={"beat_id": bi.get("current_beat_id")},
            level="WARNING",
        )

    if narration_spec:
        with room_phase(
            lf_ctx,
            "room.phase4_narrate",
            input_data={
                "purpose": narration_spec.get("purpose"),
                "timing": narration_spec.get("timing"),
                "source": "pre_actor",
            },
        ):
            # narration 在 actor 之前执行，没有 confirmed_events，
            # 用 director 的 brief + scene_facts 构造合成事件作为素材
            narration_events = _synthetic_confirmed_events_for_narration(
                narration_spec, bi
            )
            request = contracts.NarrationRequest(
                purpose=str(narration_spec.get("purpose", "visible_action")),
                timing=str(narration_spec.get("timing", "after_dialogue")),
                narration_type=str(narration_spec.get("narration_type", "旁白")),
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
                correlation_id=direct_response.message_id,
                payload=contracts.NarratorInput(
                    narration_request=request,
                    scene=bi.get("scene", {}),
                    confirmed_events=tuple(narration_events),
                    visible_facts=visible_facts,
                    previous_published_messages=public_history_tuple,
                ),
            )
            draft = contracts.to_dict(
                narrator.handle_message(narrator_request_message).payload
            )
            narration = str(draft.get("text", ""))
            # scene（含 location）由 story plan beat 定义，narrator 不再回写
            if narration and not draft.get("contains_dialogue"):
                narration_output = {
                    "role": request.narration_type,
                    "text": narration,
                    "kind": "narration",
                }
                if request.timing == "before_dialogue":
                    narration_outputs.insert(0, narration_output)
                else:
                    narration_outputs.append(narration_output)
            log_event(
                lf_ctx,
                "room.narration_result",
                output_data={
                    "chars": len(narration),
                    "accepted": bool(narration)
                    and not draft.get("contains_dialogue"),
                    "timing": request.timing,
                    "source": "pre_actor",
                },
            )

    # ═══ Phase 2b: Actor Pool 行动 ═══
    # Narrator 不在此对象池中。
    actor_results: list[dict[str, Any]] = []
    _tasks = directive.get("actor_tasks", [])
    with room_phase(
        lf_ctx,
        "room.phase2_act",
        input_data={
            "act_required": act_required,
            "task_count": len(directive.get("actor_tasks", [])),
        },
    ):
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
                    beat_action_brief=str(raw_task.get("beat_action_brief", "")),
                )
            except ValueError as exc:
                log_event(
                    lf_ctx,
                    "room.actor_task_invalid",
                    input_data=raw_task,
                    output_data={"error": str(exc)},
                    level="WARNING",
                )
                continue
            log_event(
                lf_ctx,
                "room.actor_task_start",
                input_data={
                    "target": target,
                    "task_id": task.task_id,
                    "objective": task.objective,
                },
            )
            try:
                if is_yangjian(target):
                    perception_text = state_manager.get_perception(
                        "yangjian", state, ""
                    )
                    # Inject relationship summary so yangjian knows
                    # how he feels about the user.
                    try:
                        import relationship as rel_mod
                        rel_summary = rel_mod.get_summary_for_yangjian()
                        if rel_summary:
                            perception_text = (
                                perception_text + "\n\n" + rel_summary
                            ) if perception_text.strip() else rel_summary
                    except Exception:
                        pass
                    # Inject checkpoint description if this beat has one
                    # (RelationshipCheckpoint dataclass OR plain dict)
                    checkpoint = bi.get("relationship_checkpoint")
                    if checkpoint:
                        checkpoint_desc = (
                            checkpoint.get("description", "")
                            if isinstance(checkpoint, dict)
                            else getattr(checkpoint, "description", "")
                        )
                        perception_text += (
                            "\n\n## 本回合关系评估点\n"
                            f"{checkpoint_desc}\n"
                            "请在 relationship_feedback 中输出你对用户本回合行为的感受变化"
                            "（没有变化也填，changes 留空即可）"
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
                            scene=bi.get("scene", {}),
                            public_room_history=public_history_tuple,
                            perception=perception,
                        ),
                    )
                    result = contracts.to_dict(
                        yangjian.handle_message(actor_request).payload
                    )
                    # Process relationship feedback from yangjian
                    # (only present on checkpoint beats)
                    feedback = result.get("relationship_feedback")
                    if isinstance(feedback, dict):
                        changes = feedback.get("changes", {})
                        reason = str(feedback.get("reason", ""))
                        if changes and isinstance(changes, dict):
                            try:
                                import relationship as rel_mod
                                rel_mod.apply_delta(
                                    changes,
                                    beat_id=str(bi.get("current_beat_id", "")),
                                    reason=reason,
                                )
                                log_event(
                                    lf_ctx,
                                    "room.relationship_update",
                                    output_data={
                                        "changes": changes,
                                        "reason": reason[:200],
                                        "beat_id": bi.get("current_beat_id"),
                                    },
                                )
                            except Exception:
                                pass
                else:
                    turn_input = npc.build_structured_turn_input(
                        target,
                        task=task,
                        scene=bi.get("scene", {}),
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
            except Exception as exc:
                log_error(
                    lf_ctx,
                    "room.actor_exception",
                    exc,
                    input_data={"target": target, "task_id": task.task_id},
                )
                raise
            log_event(
                lf_ctx,
                "room.actor_task_done",
                output_data={
                    "target": target,
                    "kind": result.get("kind"),
                    "result_id": result.get("result_id"),
                },
            )
            actor_results.append(result)
    log_event(
        lf_ctx,
        "room.resolve_gate",
        output_data={
            "resolve_required": resolve_required,
            "act_required": act_required,
            "actor_result_count": len(actor_results),
            "reason": resolve_gate.get("reason"),
        },
    )
    lf_flush(lf_ctx)

    forbidden_fragments = list(bi.get("forbidden_information", []))
    for task in directive.get("actor_tasks", []):
        forbidden_fragments.extend(task.get("constraints", []))

    resolve_response = None
    if resolve_required:
        log_event(
            lf_ctx,
            "room.phase3_resolve_enter",
            input_data={
                "actor_result_count": len(actor_results),
                "actor_kinds": [
                    item.get("kind") for item in actor_results
                ],
            },
        )
        lf_flush(lf_ctx)
        try:
            with room_phase(
                lf_ctx,
                "room.phase3_resolve",
                input_data={
                    "actor_result_ids": [
                        item.get("result_id") for item in actor_results
                    ]
                },
            ) as phase_bag:
                log_event(
                    lf_ctx,
                    "room.phase3_resolve_build_input",
                    input_data={
                        "directive_id": directive.get("directive_id"),
                    },
                )
                parsed_actor_results = tuple(
                    contracts.actor_turn_result_from_dict(item)
                    for item in actor_results
                )
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
                        actor_results=parsed_actor_results,
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
                        recovery_goal=str(bi.get("recovery_sub_goal", "")),
                    ),
                )
                log_event(
                    lf_ctx,
                    "room.phase3_resolve_llm_start",
                    input_data={"parsed_actor_count": len(parsed_actor_results)},
                )
                lf_flush(lf_ctx)
                resolve_response = director.handle_resolve(resolve_request)
                resolution = contracts.to_dict(resolve_response.payload)
                resolution["state_changes"] = resolution.pop(
                    "state_operations", []
                )
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
                if isinstance(phase_bag, dict):
                    phase_bag["output"] = {
                        "decision_count": len(resolution.get("decisions", [])),
                        "output_count": len(outputs),
                    }
        except Exception as exc:
            log_error(
                lf_ctx,
                "room.phase3_resolve_exception",
                exc,
                input_data={
                    "actor_results": [
                        {
                            "result_id": item.get("result_id"),
                            "kind": item.get("kind"),
                            "agent_id": item.get("agent_id"),
                        }
                        for item in actor_results
                    ]
                },
            )
            raise
        log_event(
            lf_ctx,
            "room.resolution",
            output_data={
                "decision_count": len(resolution.get("decisions", [])),
                "continuation": resolution.get("continuation"),
                "user_outcome": resolution.get("user_outcome"),
                "output_count": len(outputs),
                "confirmed_event_count": len(confirmed_events),
            },
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
        log_event(
            lf_ctx,
            "room.fast_path",
            output_data={
                "output_count": len(outputs),
                "confirmed_event_count": len(confirmed_events),
                "inline_effects": inline_effects,
            },
        )

    # narration 优先：旁白输出在 actor 输出之前
    if narration_outputs:
        outputs = list(narration_outputs) + list(outputs)

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

    # ── 钩子兜底：仅在异常情况下触发 ──
    # 正常流程中钩子由导演在 DIRECT 阶段安排，代码不干预。
    # 仅当导演走了 fallback 或所有 actor abstain 且无 narration 时，补一条环境线索。
    director_fell_back = bool(directive.get("_is_fallback"))
    all_abstained = bool(actor_results) and all(
        r.get("kind") == "abstain" for r in actor_results
    )
    if outputs and (director_fell_back or (all_abstained and narration_spec is None)):
        hook_text = _generate_hook_narration(bi, outputs, lf_ctx)
        if hook_text:
            outputs.append({"role": "线索", "text": hook_text, "kind": "narration"})
            log_event(
                lf_ctx,
                "room.hook_fallback",
                output_data={
                    "reason": "fallback_directive" if director_fell_back else "all_abstained",
                    "hook_text": hook_text[:200],
                },
            )

            # ── auto-hint：当钩子输出后，说明用户可以输入 #问题# 获取进一步提示 ──
            hint_marker = {
                "role": "提示",
                "text": "输入 #问题# 可以向提示系统提问，例如 #我可以做什么#",
                "kind": "meta",
            }
            outputs.append(hint_marker)

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

    # scene / world_day 由 story plan beat 定义，Director 不再裁定
    import story_facts as sf

    # 应用 resolution 中的状态变更，持久化到 story_state
    for change in resolution.get("state_changes", []):
        key = change.get("key", "")
        value = change.get("value")
        if key and value is not None:
            log_state_change(lf_ctx, key, value, source="resolve")
            # 持久化到 story_state
            if key.startswith("flag_"):
                flag_key = key.replace("flag_", "")
                ss_state.setdefault("flags", {})[flag_key] = value
            elif key.startswith("unlock_side_"):
                side_arc = key.replace("unlock_side_", "")
                if side_arc not in ss_state.get("unlocked_side_arcs", []):
                    ss_state.setdefault("unlocked_side_arcs", []).append(side_arc)
            elif key == "beat_goal":
                ss_state["beat_goal"] = value
                ss_state["beat_goal_met"] = True
            # 只更新 story_facts 内存
            if key.startswith("item_"):
                item = key.replace("item_", "")
                sf.set_item_location(item, str(value))
            elif key.startswith("reveal_"):
                sf.reveal_information(str(value))
            elif key.startswith("character_"):
                character = key.replace("character_", "")
                sf.set_character_state(character, str(value))
    # 保存 story_state
    ss.save_state(ss_state)

    # 推进 beat（若有）
    next_beat = resolution.get("next_beat")
    unlocked_next_beats = {
        item.get("target_id") for item in bi.get("available_transitions", [])
    }
    if next_beat and next_beat in unlocked_next_beats:
        # Check relationship requirements for the target beat
        transition_info = None
        for t in bi.get("available_transitions", []):
            if t.get("target_id") == next_beat:
                transition_info = t
                break
        reqs = transition_info.get("relationship_requirements") if transition_info else None
        reqs_met = True
        if reqs:
            try:
                import relationship as rel_mod
                reqs_met = rel_mod.check_requirements(reqs)
            except Exception:
                pass
            if not reqs_met:
                # Relationship doesn't meet requirements for this transition.
                # Try to find an alternative unlocked transition.
                alt_beats = [
                    t.get("target_id")
                    for t in bi.get("available_transitions", [])
                    if t.get("target_id") != next_beat
                ]
                if alt_beats:
                    next_beat = alt_beats[0]
                    log_event(
                        lf_ctx,
                        "room.relationship_gate_redirect",
                        output_data={
                            "original_target": resolution.get("next_beat"),
                            "redirected_to": next_beat,
                            "requirements": reqs,
                        },
                        level="WARNING",
                    )
                else:
                    # No alternative; stay on current beat
                    next_beat = None
                    log_event(
                        lf_ctx,
                        "room.relationship_gate_blocked",
                        output_data={
                            "blocked_target": resolution.get("next_beat"),
                            "requirements": reqs,
                        },
                        level="WARNING",
                    )
    if next_beat and next_beat in unlocked_next_beats:
        ss.advance_beat(ss_state, next_beat)
        # 更新 director 缓存
        bi = ss.get_current_beat_info(ss.load_state())
        director.set_story_context(bi)
        _beat_advanced = True

    # recovery 子目标自动检测：如果用户在当前回合有实质性行动，强制推进
    # 因为 recovery 子目标通常是让用户主动行动（如"向杨戬搭话"），如果用户发了消息就认为目标达成
    sub_goal_met = resolution.get("sub_goal_met", False)
    if not _beat_advanced and ss_state.get("in_recovery"):
        recovery_sub_goal = ss_state.get("recovery_sub_goal", "")
        user_turn = directive.get("user_turn", {})
        user_kind = user_turn.get("kind", "")
        # 如果用户有实质性行动（对话、物理行动），自动判定子目标达成
        if user_kind in ("dialogue", "physical_action") and user_message:
            sub_goal_met = True
            log_event(
                lf_ctx,
                "room.auto_sub_goal_met",
                output_data={
                    "reason": "user_action_detected",
                    "user_kind": user_kind,
                    "recovery_sub_goal": recovery_sub_goal,
                },
                level="WARNING",
            )
    if not _beat_advanced and ss_state.get("in_recovery") and sub_goal_met:
        rejoin_target = ss.exit_recovery_arc(ss_state)
        if rejoin_target:
            ss.advance_beat(ss_state, rejoin_target)
            bi = ss.get_current_beat_info(ss.load_state())
            director.set_story_context(bi)
            _beat_advanced = True
            log_event(
                lf_ctx,
                "room.recovery_exit",
                output_data={"rejoin_target": rejoin_target},
            )

    # 递增 tick（未推进时）
    # recovery 时递增 recovery_tick_counter，不影响 beat_tick_counter
    if not _beat_advanced and ss_state.get("status") == "active":
        if ss_state.get("in_recovery"):
            ss.increment_recovery_tick(ss_state)
        else:
            ss.increment_beat_tick(ss_state)

    # beat max_turns 检查：到达最大轮次但未推进 -> 进入 recovery
    # diversion_allowed=true 时跳过 recovery 判定
    if not _beat_advanced and not ss_state.get("in_recovery"):
        if not bi.get("diversion_allowed", False) and ss.check_beat_max_turns(ss_state):
            log_event(
                lf_ctx,
                "room.beat_max_turns_reached",
                output_data={
                    "tick_count": ss_state.get("beat_tick_counter", 0),
                    "max_turns": ss.BEAT_MAX_TURNS_DEFAULT,
                },
                level="WARNING",
            )
            _trigger_recovery(ss_state, user_message or "", resolution)

    # recovery max_turns 检查：到达最大回合但子目标未达成 -> 强行退出，推进到下一 beat
    if not _beat_advanced and ss_state.get("in_recovery"):
        if ss.check_recovery_max_turns(ss_state):
            rejoin_target = ss.exit_recovery_arc(ss_state)
            if rejoin_target:
                # narrator 写过渡剧情衔接下一 beat
                transition_text = _generate_recovery_transition(
                    bi, rejoin_target, user_message or "", ss_state
                )
                # 超时强制退出：子目标都没达成，beat goal 不可能达成，直接推进
                ss.advance_beat(ss_state, rejoin_target)
                bi = ss.get_current_beat_info(ss.load_state())
                director.set_story_context(bi)
                _beat_advanced = True
                log_event(
                    lf_ctx,
                    "room.recovery_force_exit",
                    output_data={
                        "rejoin_target": rejoin_target,
                        "transition_narration": bool(transition_text),
                    },
                    level="WARNING",
                )
                if transition_text:
                    narration_outputs.append({
                        "role": "场景",
                        "text": transition_text,
                        "kind": "narration",
                    })

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
        "scene": state.get("scene", {}),
        "order": order,
        "outcome": resolution.get("next_beat", ""),
    }

    room_llm.clear_trace_context()
    log_event(
        lf_ctx,
        "room.publish",
        output_data={
            "ok": True,
            "roles": order,
            "outputs": [
                {
                    "role": item.get("role"),
                    "kind": item.get("kind"),
                    "chars": len(str(item.get("text", ""))),
                    "preview": str(item.get("text", ""))[:120],
                }
                for item in outputs
            ],
        },
    )
    # ── 最终状态快照：记录 agent 行动后的最新 room 状态 ──
    final_ss = ss.load_state()
    final_scene = state.get("scene", {}) or {}
    log_event(
        lf_ctx,
        "room.final_state",
        output_data={
            "tick": state.get("tick", 0),
            "world_day": state.get("world_day", 1),
            "scene": {
                "location": final_scene.get("location", ""),
                "weather": final_scene.get("weather", ""),
                "time_of_day": final_scene.get("time_of_day", ""),
                "mood": final_scene.get("mood", ""),
            },
            "beat": {
                "story_id": final_ss.get("story_id", ""),
                "status": final_ss.get("status", ""),
                "current_beat_id": final_ss.get("current_beat_id", ""),
                "beat_tick_counter": final_ss.get("beat_tick_counter", 0),
                "main_progress": final_ss.get("main_progress", 0.0),
                "completed_beats": list(final_ss.get("completed_beats", [])),
                "completed_endings": list(final_ss.get("completed_endings", [])),
            },
            "recovery": {
                "in_recovery": final_ss.get("in_recovery", False),
                "recovery_arc_id": final_ss.get("recovery_arc_id"),
                "recovery_rejoin_target": final_ss.get("recovery_rejoin_target"),
                "recovery_sub_goal": final_ss.get("recovery_sub_goal", ""),
                "recovery_tick_counter": final_ss.get("recovery_tick_counter", 0),
            },
            "relationship": dict(final_ss.get("relationship", {})),
        },
    )
    # ── trace 结束：记录最终 story state + recovery 状态快照 ──
    final_ss = ss.load_state()
    log_event(
        lf_ctx,
        "room.trace_end",
        output_data=_story_state_snapshot(final_ss, state),
    )
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
    bi: dict[str, Any] | None = None,
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
    # 兜底：无 brief/scene_facts 时用 beat 目的作为叙事锚点，
    # 保证 narrator 始终有素材可写（旁白是用户的眼睛，不能空转）
    if not events and bi:
        beat_goal = str(bi.get("beat_goal", "")).strip()
        beat_plot = str(bi.get("beat_plot", "")).strip()
        anchor = beat_goal or beat_plot
        if anchor:
            events.append({
                "event_id": "confirmed_director_beat_anchor",
                "event_type": "scene_anchor",
                "summary": anchor[:150],
                "participants": [],
                "fact_ids": [],
            })
        scene = str(bi.get("current_scene", "")).strip()
        if not scene:
            scene = str(bi.get("current_beat_id", "")).strip()
        if scene:
            events.append({
                "event_id": "confirmed_director_scene_id",
                "event_type": "scene_anchor",
                "summary": f"用户此刻位于：{scene}",
                "participants": [],
                "fact_ids": [],
            })
    return events


def _select_narration_spec(
    directive: dict[str, Any],
    resolution: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """从 directive.narration_request 构建 narration spec。
    
    注意：user_outcome.presentation 已废弃，narration 只通过 directive.narration_request 控制。
    """
    narration_request = directive.get("narration_request")
    if isinstance(narration_request, Mapping):
        return {
            "purpose": narration_request.get("purpose", "visible_action"),
            "timing": narration_request.get("timing", "after_dialogue"),
            "narration_type": narration_request.get("narration_type", "旁白"),
            "visible_fact_ids": list(
                narration_request.get("visible_fact_ids", [])
            ),
            "max_characters": narration_request.get("max_characters", 100),
            "style_profile": narration_request.get("style_profile", "concise"),
            "brief": narration_request.get("brief", ""),
            "scene_facts": list(narration_request.get("scene_facts", [])),
        }
    return None


def _generate_hook_narration(
    bi: dict[str, Any],
    outputs: list[dict[str, Any]],
    lf_ctx,
) -> str:
    """异常兜底：当导演 fallback 或所有 actor abstain 时，生成一条环境线索作为钩子。

    基于当前 beat 的推进方向和允许透露的信息，调用旁白 LLM 生成一句环境变化描述。
    正常流程不调用此函数。
    """
    import llm as room_llm

    transitions = bi.get("available_transitions", [])
    allowed_info = bi.get("allowed_information", [])
    beat_plot = bi.get("beat_plot", "")
    beat_id = bi.get("current_beat_id", "")

    # 构造推进方向描述
    if transitions:
        hint_parts = []
        for t in transitions:
            target = t.get("target_id", "")
            consequences = t.get("preserved_consequences", [])
            if consequences:
                hint_parts.append(f"向「{target}」推进：{', '.join(consequences)}")
            else:
                hint_parts.append(f"向「{target}」推进")
        hints = "; ".join(hint_parts)
    else:
        hints = "保持当前局面开放"

    # 已有输出摘要（避免重复）
    existing = " | ".join(
        f"{o.get('role', '')}: {str(o.get('text', ''))[:80]}"
        for o in outputs[-3:]
    )

    system = (
        "你是杨戬项目的旁白。现在需要一个环境线索作为钩子，让用户知道接下来可以关注什么。"
        "只描述环境变化或新出现的线索，不替任何角色说话或行动。"
        "用第二人称\"你\"视角。一到两句话，不超过80字。"
        "不要使用比喻、明喻或诗化意象。"
    )
    prompt = (
        f"当前 Beat：{beat_id}\n"
        f"Beat 目的：{beat_purpose[:200]}\n"
        f"推进方向：{hints}\n"
        f"可透露的信息：{', '.join(allowed_info)}\n"
        f"已有输出（不要重复）：{existing[:300]}\n\n"
        f"请写一句环境变化或线索提示，给用户一个可以继续参与的入口。"
    )

    try:
        raw = room_llm.call(
            agent_id="narrator.hook",
            system=system,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=800,
        )
        text = str(raw or "").strip()
        if not text or text in ("", '""', "''", "（空）", "(空)"):
            return ""
        return text[:100]
    except Exception:
        return ""


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
            # Prefer director-confirmed text; if omitted, keep actor proposal
            # so modify never silently drops user-visible dialogue/action.
            dialogue = (
                decision.get("final_dialogue")
                or proposal.get("dialogue")
            )
            action = (
                decision.get("final_action")
                or proposal.get("action")
            )
        else:
            # accept: prefer director-confirmed text; fall back to actor proposal.
            # Must use `or` not dict.get(default) because the key may exist
            # with value null (LLM outputs "final_dialogue": null).
            dialogue = (
                decision.get("final_dialogue")
                or proposal.get("dialogue")
            )
            action = (
                decision.get("final_action")
                or proposal.get("action")
            )
        agent_id = str(actor_result.get("agent_id", ""))
        role = display_agent_name(agent_id) or agent_id
        outcome = str(decision.get("outcome_summary", "")).strip()
        event_id = f"confirmed_{result_id or index + 1}"
        if outcome:
            confirmed_events.append({
                "event_id": event_id,
                "event_type": "actor_result",
                "summary": outcome,
                "participants": [normalize_agent_id(agent_id) or agent_id],
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
                        None if is_yangjian(agent_id) else agent_id
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
                        None if is_yangjian(agent_id) else agent_id
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


def _tick_free_chat(state, user_message=None, source="cron", lf_ctx=None):
    """自由聊天模式：故事线已完成，不走 beat 推进，直接让杨戬回应用户。
    铁律：不传任何故事线上下文（event_log / scene / beat_info）。
    """
    outputs = []
    if user_message:
        # 关系是独立系统，可以保留；其余全部清空
        rel_summary = ""
        try:
            import relationship as rel_mod
            rel_summary = rel_mod.get_summary_for_yangjian() or ""
        except Exception:
            pass
        from contracts import YangJianTurnInput, AgentTask, FactRef, AgentRef, AgentKind, Phase, new_message

        task = AgentTask(
            task_id="free_chat",
            target_agent_id="yangjian",
            objective="故事已完结，与用户自由聊天。保持杨戬人设。",
            source_reference="free_chat",
        )
        # 彻底清空故事线上下文
        public_history: tuple = ()
        perception: tuple = ()
        if rel_summary:
            perception = (FactRef(fact_id="free", text=rel_summary, visibility="private"),)
        turn_input = YangJianTurnInput(
            task=task,
            scene={},
            public_room_history=public_history,
            perception=perception,
        )
        turn_id = f"turn_{state.get('tick', 0) + 1}"
        actor_request = new_message(
            turn_id=turn_id,
            story_id="free_chat",
            beat_id="free_chat",
            phase=Phase.ACT,
            sender=AgentRef(agent_id="room", kind=AgentKind.ROOM),
            recipient=AgentRef(agent_id="yangjian", kind=AgentKind.ACTOR),
            message_type="yangjian.turn.input",
            payload=turn_input,
        )
        try:
            result = yangjian.act_turn(turn_input, minimal=True)
            proposal = result.get("proposal")
            if proposal:
                action = proposal.get("action")
                if action and isinstance(action, dict):
                    desc = action.get("description", "").strip()
                    if desc:
                        outputs.append({"role": "杨戬的动作", "text": desc})
                dialogue = proposal.get("dialogue")
                if dialogue and isinstance(dialogue, dict):
                    text = dialogue.get("text", "").strip()
                    if text:
                        outputs.append({"role": "杨戬", "text": text})
            elif result.get("abstention"):
                # Abstain - skip silently or log
                pass
        except Exception as exc:
            log_error(lf_ctx or LangfuseCtx(), "room.free_chat_exception", exc)
            outputs.append({"role": "系统", "text": "【杨戬暂时没有回应】"})

    state["tick"] = state.get("tick", 0) + 1
    if user_message:
        state.setdefault("event_log", []).append(
            f"[tick{state['tick']}] 用户: {user_message[:120]}"
        )
    for o in outputs:
        if o["text"]:
            state.setdefault("event_log", []).append(
                f"[tick{state['tick']}] {o['role']}: {o['text'][:200]}"
            )
    state_manager.save(state)
    log_event(
        lf_ctx or LangfuseCtx(),
        "room.free_chat",
        output_data={"tick": state["tick"], "output_count": len(outputs)},
    )
    return {
        "ok": True,
        "output": outputs,
        "state": state,
        "decision": {"mode": "free_chat"},
    }


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
    # scene / world_day 由 story plan beat 定义，Director 不再裁定
    changes = {k: v for k, v in changes.items()
               if k not in ("weather", "current_weather", "mood", "world_day")}
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
            # beat max_turns 检查
            if not ss_state.get("in_recovery") and ss.check_beat_max_turns(ss_state):
                _trigger_recovery(ss_state, user_message, decision)

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
            print(f"Beat: {bi.get('current_beat_id', '?')} — {bi.get('beat_plot', '?')[:60]}")
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
