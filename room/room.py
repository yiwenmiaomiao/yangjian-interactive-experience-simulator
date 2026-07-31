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
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_manager, story_engine, director, narrator, yangjian, npc_manager_runtime as npc
import runtime_context

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

    # 刷新导演上下文
    bi = ss.get_current_beat_info(ss_state)
    if "error" not in bi:
        active_npcs = []
        for requirement in ss.get_current_npc_requirements(ss_state):
            npc_id = npc.acquire_for_side_arc(
                requirement, requirement.side_arc_id
            )
            if not npc_id:
                continue
            try:
                npc.activate(
                    npc_id,
                    story_id=requirement.story_id,
                    side_arc_id=requirement.side_arc_id,
                    scene_id=bi.get("current_beat_id", ""),
                    reason=f"Required by {requirement.requirement_id}",
                )
                active_npcs.append(npc_id)
            except Exception:
                continue
        bi = dict(bi)
        bi["active_npcs"] = active_npcs
        director.set_story_context(bi)

    # Phase 1: DIRECTOR DIRECT
    directive = director.decide_direct(state, user_message)

    # 从 directive 构建 order（新格式：allowed_speakers）
    allowed_speakers = directive.get("allowed_speakers", ["杨戬", "用户"])
    allowed_runtime_roles = {
        "旁白",
        "杨戬",
        "用户",
        *directive.get("task_to_npcs", {}).keys(),
    }
    order = [
        role for role in allowed_speakers
        if role in allowed_runtime_roles
    ]
    if "用户" not in order:
        order.append("用户")

    # 旁白是否需要
    has_narration = "旁白" in order

    # 杨戬任务
    yangjian_task = directive.get("task_to_yangjian", "")
    yangjian_info = directive.get("info_to_yangjian", [])

    # NPC 任务
    npc_tasks = directive.get("task_to_npcs", {})
    npc_infos = directive.get("info_to_npcs", {})

    # must_not
    must_not = directive.get("must_not", [])

    # Phase 2: Agent 行动
    outputs = []
    for role in order:
        if role == "旁白" and has_narration:
            # 旁白必须等待 RESOLVE 完成，只描述已经确认发生的结果。
            continue

        elif role == "杨戬":
            from story_facts import get_facts_summary
            perception = state_manager.get_perception("yangjian", state, "")
            # 导演给杨戬的信息
            if yangjian_info:
                perception += "\n\n## 导演提供的信息\n" + "\n".join(f"- {info}" for info in yangjian_info)
            facts_summary = get_facts_summary()
            if facts_summary:
                perception += f"\n\n## 当前已确认的事实\n{facts_summary}"
            if user_message:
                perception += f"\n\n## 用户刚刚说\n{user_message}"
            # 导演的任务（局面要求）
            task_info = f"【导演局面要求】{yangjian_task}" if yangjian_task else ""
            result = yangjian.act({
                "scene": directive.get("current_beat", ""),
                "outcome": task_info,
                "goals": {"杨戬": yangjian_task or "回应当前局面"},
            }, perception)
            for a in result.get("actions", []):
                outputs.append({"role": "杨戬的动作", "text": a})
            for d in result.get("dialogues", []):
                outputs.append({"role": "杨戬", "text": d})

        elif role in npc_tasks:
            npc_task = npc_tasks[role]
            npc_info = npc_infos.get(role, [])
            recent_events = []
            if user_message:
                recent_events.append(user_message)
            result = npc.act(role, {
                "npc_tasks": {role: {"objective": npc_task, "allowed_actions": ["speak", "act"], "must_not": must_not, "visible_events": npc_info + recent_events}},
                "outcome": npc_task,
                "scene": directive.get("current_beat", ""),
                "goals": {},
            }, "")
            for a in result.get("actions", []):
                outputs.append({"role": f"{role}的动作", "text": a, "npc_id": role})
            for d in result.get("dialogues", []):
                outputs.append({"role": role, "text": d, "npc_id": role})

        elif role == "用户":
            pass  # 等待用户输入

    # Phase 3: DIRECTOR RESOLVE
    proposals = [
        {
            "proposal_id": f"proposal_{index + 1}",
            "role": output["role"],
            "text": output["text"],
            "kind": "action" if output["role"].endswith("的动作") else "dialogue",
            "npc_id": output.get("npc_id"),
        }
        for index, output in enumerate(outputs)
        if output["role"] not in ("旁白", "用户")
    ]
    resolution = director.decide_resolve(state, proposals, user_message)

    # RESOLVE 是最终裁决：reject 不发布，modify 的动作使用裁决事实，
    # dialogue 的 modify 保留已经说出的原话，但结果仍由 outcome_summary 表达。
    forbidden_fragments = [
        *bi.get("forbidden_reveals", []),
        *must_not,
    ]
    outputs, resolved_outcomes = _apply_resolution(
        proposals, resolution, forbidden_fragments
    )
    proposal_by_id = {p["proposal_id"]: p for p in proposals}
    for decision in resolution.get("decisions", []):
        if decision.get("result") not in {"accept", "modify"}:
            continue
        proposal = proposal_by_id.get(decision.get("proposal_id"))
        if proposal and proposal.get("npc_id"):
            npc.record_accepted(
                proposal["npc_id"],
                event_id=f"tick_{state.get('tick', 0) + 1}_{proposal['proposal_id']}",
                summary=str(decision.get("outcome_summary", "")),
            )

    # 旁白只接收已裁决结果和统一公共事实。
    if has_narration and resolved_outcomes:
        from story_facts import get_facts_summary
        narration = narrator.speak({
            "scene": directive.get("current_beat", ""),
            "mood": "",
            "outcome": "；".join(resolved_outcomes),
            "order": ["旁白"],
            "facts_summary": get_facts_summary(),
        }, state, max_chars=100)
        if narration and not _contains_forbidden(narration, forbidden_fragments):
            outputs.append({"role": "旁白", "text": narration})

    # Phase 4: Room 保存 + 事实管理
    state["tick"] = state.get("tick", 0) + 1
    if user_message:
        state.setdefault("event_log", []).append(f"[tick{state['tick']}] 用户: {user_message[:120]}")
    for o in outputs:
        if o["text"]:
            state.setdefault("event_log", []).append(f"[tick{state['tick']}] {o['role']}: {o['text'][:200]}")

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
