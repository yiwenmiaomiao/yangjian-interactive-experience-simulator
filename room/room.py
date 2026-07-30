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
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state_manager, story_engine, director, narrator, yangjian, npc_manager_runtime as npc

PROFILE_DIR = os.path.expanduser("/Users/xiaoxianhan/Documents/yangjian-room")

# 是否启用故事计划模式
_story_plan_active = False


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
        if beats_raw and all(b.get("beat_id") and b.get("purpose") for b in beats_raw):
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


# ── 主循环 ──────────────────────────────────────────────────


def tick(user_message=None, source="cron"):
    """
    执行一个 Room Tick。
    
    Args:
        user_message: 用户输入文本，None 表示定时推动
        source: 触发源 "cron" 或 "user"
    
    Returns:
        dict: {"ok": bool, "output": [...], "state": {...}, "decision": {...}}
    """
    try:
        state = state_manager.load()

        # ── 两阶段调度：故事计划模式走 DIRECT → RESOLVE ──
        if _story_plan_active:
            return _tick_two_stage(state, user_message, source)

        # ── 传统模式（一次输出） ──
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
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(e),
            "output": [{"role": "系统", "text": f"【Room 异常: {e}】"}],
        }


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

    # 刷新导演上下文
    bi = ss.get_current_beat_info(ss_state)
    if "error" not in bi:
        director.set_story_context(bi)

    # Phase 1: DIRECTOR DIRECT
    directive = director.decide_direct(state, user_message)

    # 从 directive 构建 order（新格式：allowed_speakers）
    allowed_speakers = directive.get("allowed_speakers", ["杨戬", "用户"])
    order = list(allowed_speakers)

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
            text = narrator.speak({
                "scene": directive.get("current_beat", ""),
                "mood": "",
                "outcome": directive.get("beat_purpose", ""),
                "order": ["旁白"],
                "goals": {},
            }, state, max_chars=200)
            if text:
                outputs.append({"role": "旁白", "text": text})

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
                outputs.append({"role": f"{role}的动作", "text": a})
            for d in result.get("dialogues", []):
                outputs.append({"role": role, "text": d})

        elif role == "用户":
            pass  # 等待用户输入

    # Phase 3: DIRECTOR RESOLVE
    proposals = [{"role": o["role"], "text": o["text"], "npc_id": o["role"]} for o in outputs if o["role"] not in ("旁白", "用户")]
    resolution = director.decide_resolve(state, proposals, user_message)

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
            state_manager.apply_changes(state, {key: value})
            from langfuse_logger import log_state_change
            log_state_change(lf_ctx, key, value, source="resolve")
            if key.startswith("item_"):
                item = key.replace("item_", "")
                sf.set_item_location(item, str(value))
            elif key.startswith("reveal_"):
                sf.reveal_information(str(value))

    # 推进 beat（若有）
    next_beat = resolution.get("next_beat")
    if next_beat:
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

    return {
        "ok": True,
        "output": outputs,
        "state": state,
        "decision": decision_out,
        "directive": directive,
        "resolution": resolution,
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
