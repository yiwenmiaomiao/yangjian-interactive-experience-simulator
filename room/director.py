"""
导演 Agent（织梦者）
职责：调度 agent 和更新空间状态，不写剧情。

8 条规则：
1. 先判断当前可用节点。
2. 再解释用户行为。
3. 判断是否进入分支。
4. 将任务转换成符合角色动机的局面要求。
5. 不为杨戬预写完整回答。
6. 不向 NPC 广播完整故事。
7. 不允许角色自行修改故事状态。
8. 只有状态机可以提交正式节点变化。

导演和 Room 之间使用结构化状态机输入输出。
"""
from __future__ import annotations

import json, os, sys, time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm, story_engine
if __package__:
    from . import contracts
else:
    import contracts
from agent_ids import (
    actor_display_map,
    build_available_actor_pool,
    normalize_agent_id,
    reset_available_targets,
    set_available_targets,
)
from agent_schemas import (
    DirectorDirectiveOutput,
    DirectorResolutionOutput,
    StructuredOutputError,
    call_structured,
)
from director_control import (
    DirectorContext,
    validate_directive,
    validate_resolution,
)

# ── 故事计划模式状态 ──────────────────────────────────────

_STORY_PLAN_ACTIVE = False
_CACHED_BEAT_INFO: dict[str, Any] | None = None


def set_story_context(beat_info: dict[str, Any] | None) -> None:
    global _STORY_PLAN_ACTIVE, _CACHED_BEAT_INFO
    if beat_info and "error" not in beat_info:
        _STORY_PLAN_ACTIVE = True
        _CACHED_BEAT_INFO = beat_info
    else:
        _STORY_PLAN_ACTIVE = False
        _CACHED_BEAT_INFO = None


# ── 系统提示词 ────────────────────────────────────────────

SYSTEM_PROMPT = """你是杨戬 Room 的导演。

你不是角色，不是旁白，不写剧情，不写对白，不写场景描写。

## 你的职责

你只做以下 8 件事：
1. 判断当前可用节点（哪些 beat/分支已解锁）。
2. 解释用户行为（用户说了什么、做了什么、想达到什么）。
3. 判断是否进入分支（用户行为是否满足某个分支条件）。
4. 将任务转换成符合角色动机的局面要求（告诉角色"面对什么局面"，不告诉角色"说什么话"）。
5. 不为杨戬预写完整回答。
6. 不向 NPC 广播完整故事（NPC 只知道自己该知道的）。
7. 不允许角色自行修改故事状态。
8. 只有状态机可以提交正式节点变化（你只提议，Room 决定是否生效）。

## 你绝对不做的事

- ❌ 不写剧情文本（"薄雾弥漫的清晨，杨戬缓步走来..."）→ 这是旁白的 job
- ❌ 不写角色对白（"杨戬说：你来了"）→ 这是角色的 job
- ❌ 不写场景描写（"桃山脚下，晨雾缭绕"）→ 这是旁白的 job
- ❌ 不替用户决定行动或感情
- ❌ 不进入未解锁的剧情节点
- ❌ 不向角色透露该角色不该知道的信息
- ❌ 不推翻已发生的事实

## 你输出的内容

你输出结构化的调度指令，告诉 Room：
- 当前 beat 是什么
- 给 Actor Pool 中哪些角色什么任务
- 是否要求 NPC Manager 注册、激活、停用或完成某个 StoryPlan Profile
- 裁决后是否需要独立 Narrator 描述确认事件
- 当角色都请求不行动时，可使用哪个已授权外部事件继续发展

## 任务描述规则

- tasks.target / actor_tasks 的 target 必须填英文 agent_id，不能填中文显示名
- 杨戬的 agent_id 固定为：yangjian（面向用户的文案里可以说“杨戬”，结构化字段只能写 yangjian）
- actor_tasks.objective 描述角色"面对什么局面"，不描述"说什么话"
- 例如：✅ target="yangjian", objective="注意到用户对古盒的异常感兴趣，需要做出反应"
- 例如：❌ target="杨戬"（中文显示名不能出现在 target 字段）
- 例如：❌ "杨戬说：这不过是些古老的纹路"
- information_ids 只能逐字复制 Room 提供的“允许透露的信息”，不能扩写
- Narrator 不属于 Actor Pool，不能出现在 actor_tasks 中
- 你不能请求 hold 或停止。角色可以请求不行动，但你必须继续裁决

## narration 规则
- required=true 时 purpose/timing/visible_facts/max_characters 才生效
- purpose 只能是 scene_opening / transition / visible_action / external_event / closing / none
- timing 只能是 before_dialogue / after_dialogue / none，不要用 immediate
- visible_facts 只能填 allowed_information 中的 fact_id；场景描写写在 purpose 文本中
- 用户正在与杨戬连续对话时通常 required=false
- Narrator 不属于 Actor Pool，不要给旁白创建 task
- 当场景发生变化（进入新地点、环境突变、发现新事物）时，narration.required 必须为 true，旁白是用户的眼睛，必须描述用户看到的新环境

## user_turn 规则
先分类用户本回合输入，再派任务：
- dialogue：对角色说话，disclosure.required=false
- physical_action：用户物理行动（如【打开盒子】），若结果需公开则 disclosure.required=true
- declarative_choice：立场/分支选择，disclosure.required=false，通常走 fast path
- passive：嗯/好的等，disclosure.required=false
- meta：系统指令

## resolve_gate 规则（决定是否调用 RESOLVE LLM）
- required=false（fast path）：简单对话、被动回应、确定性分支、StoryPlan 已明确的结果
- required=true（full path）：物理行动有歧义、多角色冲突、advance、高风险揭露、需要 modify/reject
- act_required=false 时可不派 Actor 任务（如纯被动回应）
- 不确定时默认 required=true

## inline_effects 规则（仅 resolve_gate.required=false 时可用）
- state_operations：静默状态更新（分支选择、计数等），不触发旁白
- user_feedback：确定性环境反馈；disclosure.required=true 时必须提供
- required=true 时 inline_effects 必须为空（state_operations=[]，user_feedback=null）

## 钩子规则（每回合必须遵守）
每回合的输出组合必须包含至少一个未闭合的入口，让用户知道"接下来可以做什么"。
钩子来源有优先级，优先用旁白和 NPC，杨戬只在必要时才作为钩子来源：

钩子来源优先级：
1. 旁白（最优先）：环境发生变化（远处传来声音、光线突然变化、新物体出现、天气骤变）
2. NPC（次优先）：NPC打断、带来新信息、做出意外举动
3. 杨戬（末选）：只在旁白和NPC都不适合本回合时，才由杨戬留钩子——且必须符合他高傲寡言的人设（一个意味深长的动作、一句点到为止的话），不要让他主动追问或讨好用户

杨戬是高傲寡言的角色，多数回合他说完该说的话就完了，不需要他负责"留钩子"。
不要让一个回合的输出以完全闭合的状态结尾。
如果本回合 actor_tasks 的结果不足以构成钩子，应在 narration_request 中安排一个环境变化或线索提示。
参考"可引导方向"字段，通过角色行为或环境变化自然引导用户接近推进条件，不要直接告诉用户"你需要做X"。
"""

# ── 故事计划上下文注入 ──────────────────────────────────

STORY_CONTEXT_TEMPLATE = """

--- Room 提供的当前上下文 ---

当前 Beat：{beat_id}
Beat 目的：{beat_purpose}

允许透露的信息：{allowed_info}
禁止透露的信息：{forbidden_reveals}

可用分支目标：{transitions}
可引导方向（不要直接告诉用户，通过角色行为或环境变化创造让用户自然接近的机会）：{advance_hints}
{side_arcs_section}
可调度 Actor ID（tasks.target 必须从这里原样复制）：{available_actor_ids}
Actor 显示名对照（仅供理解，禁止写入 target）：{actor_display_map}
当前 Beat 的 NPC Profile（仅 id，完整档案在 NPC Manager）：{npc_profiles}
已有故事事实：{consequences}
连续偏离次数：{deviation_count}
{recovery_note}
当前杨戬与用户关系：{relationship_summary}

--- 用户消息 ---
{user_message_display}

--- 请输出结构化调度指令 ---
"""


# ── 入口 ──────────────────────────────────────────────────


def decide(state, user_message=None) -> dict[str, Any]:
    """传统模式：一次输出。"""
    if _STORY_PLAN_ACTIVE and _CACHED_BEAT_INFO:
        return _decide_story(state, user_message)
    return _decide_traditional(state, user_message)


def decide_direct(state, user_message=None) -> dict[str, Any]:
    """故事计划模式：DIRECT 阶段"""
    if not (_STORY_PLAN_ACTIVE and _CACHED_BEAT_INFO):
        return {"error": "no_story_context"}
    return _decide_story(state, user_message)


def decide_resolve(
    state,
    proposals: list[dict[str, Any]],
    user_message=None,
    user_turn: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """故事计划模式：RESOLVE 阶段"""
    if not (_STORY_PLAN_ACTIVE and _CACHED_BEAT_INFO):
        return {"error": "no_story_context"}
    return _resolve_story(state, proposals, user_message, user_turn)


def handle_direct(
    message: contracts.AgentMessage[contracts.DirectorDirectInput],
) -> contracts.AgentMessage[contracts.DirectorDirective]:
    """Envelope-based DIRECT endpoint used by Room."""
    if message.phase is not contracts.Phase.DIRECT:
        raise ValueError("Director DIRECT received a message in the wrong phase")
    user_message = str(message.payload.user_event.get("text", "")) or None
    raw = decide_direct(dict(message.payload.world_snapshot), user_message)
    payload = contracts.director_directive_from_dict(raw)
    return contracts.new_message(
        turn_id=message.turn_id,
        story_id=message.story_id,
        beat_id=message.beat_id,
        phase=contracts.Phase.DIRECT,
        sender=message.recipient,
        recipient=message.sender,
        message_type="director.directive",
        correlation_id=message.message_id,
        payload=payload,
    )


def handle_resolve(
    message: contracts.AgentMessage[contracts.DirectorResolveInput],
) -> contracts.AgentMessage[contracts.DirectorResolution]:
    """Envelope-based RESOLVE endpoint used by Room."""
    if message.phase is not contracts.Phase.RESOLVE:
        raise ValueError("Director RESOLVE received a message in the wrong phase")
    raw_results = [
        contracts.to_dict(item) for item in message.payload.actor_results
    ]
    raw = decide_resolve(
        dict(message.payload.world_snapshot),
        raw_results,
        str(message.payload.user_event.get("text", "")) or None,
        dict(message.payload.user_turn),
    )
    if not isinstance(raw, dict) or raw.get("error") or "continuation" not in raw:
        # decide_resolve can return {"error": ...}; never let Room crash here.
        # Fail soft: accept proposals so dialogue is not silently dropped.
        bi = _CACHED_BEAT_INFO or {
            "story_id": message.story_id,
            "current_beat_id": message.beat_id,
        }
        raw = _fallback_resolution(
            raw_results,
            bi,
            reason=str(
                (raw or {}).get("error", "resolve_failed")
                if isinstance(raw, dict)
                else "resolve_failed"
            ),
        )
        # Envelope uses result_id in decisions via director_resolution_from_dict.
        for decision in raw.get("decisions", []):
            if "proposal_id" in decision and "result_id" not in decision:
                decision["result_id"] = decision["proposal_id"]
    payload = contracts.director_resolution_from_dict(raw)
    return contracts.new_message(
        turn_id=message.turn_id,
        story_id=message.story_id,
        beat_id=message.beat_id,
        phase=contracts.Phase.RESOLVE,
        sender=message.recipient,
        recipient=message.sender,
        message_type="director.resolution",
        correlation_id=message.message_id,
        payload=payload,
    )


# ── 传统模式 ─────────────────────────────────────────────


def _decide_traditional(state, user_message=None) -> dict[str, Any]:
    active_stories = story_engine.get_active_stories(state)
    stories_summary = story_engine.get_story_summary(state)

    user_content = f"用户消息：{user_message}" if user_message else "无用户输入"
    situation = f"""场景：{state.get('weather', '晴')}，{state.get('mood', '平静')}，第{state.get('world_day', 1)}天
当前活跃故事线：
{stories_summary}
{user_content}"""

    raw = llm.call(
        agent_id="director",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": situation}],
        temperature=0.7,
        max_tokens=8000,
    )
    directive = _parse_directive(raw)
    if "error" not in directive:
        directive.setdefault("order", directive.get("allowed_speakers", []))
    return directive


# ── 故事计划模式 ─────────────────────────────────────────


def _get_relationship_summary() -> str:
    """Compact relationship summary for director context (read-only)."""
    try:
        import relationship as rel_mod
        return rel_mod.get_summary_for_director()
    except Exception:
        return "未知"


def _decide_story(state, user_message=None) -> dict[str, Any]:
    """故事计划模式：输出结构化调度指令。"""
    bi = _CACHED_BEAT_INFO
    if not bi:
        return _fallback_directive()

    side_arcs_list = [a["arc_id"] for a in bi.get("available_side_arcs", [])]
    side_text = f"可进入副线: {side_arcs_list}" if side_arcs_list else "无"
    trans_text = ", ".join(f"{t['transition_id']}->{t['target_id']}" for t in bi.get("available_transitions", []))

    # 构造推进方向自然语言提示
    transitions = bi.get("available_transitions", [])
    if transitions:
        hint_parts = []
        for t in transitions:
            target = t.get("target_id", "")
            consequences = t.get("preserved_consequences", [])
            if consequences:
                hint_parts.append(f"向「{target}」推进——{', '.join(consequences)}")
            else:
                hint_parts.append(f"向「{target}」推进")
        advance_hints = "; ".join(hint_parts)
    else:
        advance_hints = "当前 beat 无明确推进方向，保持开放"

    user_msg_display = f"用户说：{user_message}" if user_message else "（无用户输入，系统推动）"
    available_ids = build_available_actor_pool(bi)
    display_map = actor_display_map(available_ids) or "yangjian=杨戬"
    pool_token = set_available_targets(available_ids)

    context = STORY_CONTEXT_TEMPLATE.format(
        beat_id=bi.get("current_beat_id", ""),
        beat_purpose=bi.get("beat_purpose", ""),
        allowed_info=", ".join(bi.get("allowed_information", [])),
        forbidden_reveals=", ".join(bi.get("forbidden_reveals", [])),
        transitions=trans_text,
        advance_hints=advance_hints,
        side_arcs_section=side_text,
        available_actor_ids=", ".join(available_ids),
        actor_display_map=display_map,
        npc_profiles=json.dumps(
            bi.get("npc_profiles", []), ensure_ascii=False
        ),
        consequences=", ".join(bi.get("consequences", [])),
        deviation_count=bi.get("beat_tick_counter", 0),
        recovery_note="",
        relationship_summary=_get_relationship_summary(),
        user_message_display=user_msg_display,
    )

    prompt = (
        f"{context}\n"
        f"天气：{state.get('weather', '晴')} "
        f"氛围：{state.get('mood', '平静')} "
        f"第{state.get('world_day', 1)}天"
    )

    attempt_messages: list[dict[str, str]] = [
        {"role": "user", "content": prompt}
    ]
    last_fail = "no_attempt"
    try:
        for attempt in range(3):
            try:
                parsed = call_structured(
                    DirectorDirectiveOutput,
                    agent_id="director.direct",
                    system=SYSTEM_PROMPT,
                    messages=attempt_messages,
                    temperature=0.7,
                    max_tokens=8000,
                    target_pool=available_ids,
                )
            except StructuredOutputError as exc:
                last_fail = f"structured_error:{exc}"
                if attempt < 2:
                    attempt_messages = attempt_messages + [
                        {
                            "role": "user",
                            "content": (
                                "上一次结构化输出失败，请重新输出完整 JSON。"
                                " tasks.target 只能从池中选择："
                                f"{json.dumps(list(available_ids), ensure_ascii=False)}"
                            ),
                        }
                    ]
                continue
            canonical = _coerce_canonical_directive(parsed.model_dump(), bi)
            if "error" not in canonical:
                canonical = _enrich_canonical_directive(canonical, bi)
                canonical = _sanitize_canonical_directive(canonical, bi)
                validation = validate_directive(
                    canonical, _build_director_context(bi)
                )
                if validation.is_valid:
                    runtime = _canonical_directive_to_runtime(canonical, bi)
                    runtime["current_story_id"] = bi.get("story_id", "story_1")
                    runtime["current_beat"] = bi.get("current_beat_id", "")
                    return runtime
                issues = ";".join(
                    f"{i.location}:{i.message}" for i in validation.issues[:5]
                )
                last_fail = f"guard_invalid:{issues}"
                if attempt < 2:
                    attempt_messages = attempt_messages + [
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                parsed.model_dump(), ensure_ascii=False
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Guard 校验失败，请只修正结构化 JSON 后重发。"
                                f" 问题：{issues}。"
                                " tasks.target / user_turn.target 只能从当前池选择："
                                f"{json.dumps(list(available_ids), ensure_ascii=False)}"
                            ),
                        },
                    ]
                    continue
            else:
                last_fail = f"coerce_error:{canonical.get('error')}"
                if attempt < 2:
                    continue

        print(
            f"[director] DIRECT fallback_full_path after retries: {last_fail}",
            flush=True,
        )
        return _fallback_directive()
    finally:
        reset_available_targets(pool_token)


def _resolve_story(
    state,
    proposals: list[dict[str, Any]],
    user_message=None,
    user_turn: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """RESOLVE 阶段：裁决角色提议与用户行动。"""
    bi = _CACHED_BEAT_INFO
    if not bi:
        return {"error": "no_story_context"}

    resolve_prompt = """你是导演，现在进入 RESOLVE（裁决）阶段。

你收到本回合所有角色的结构化结果，以及 DIRECT 阶段识别的用户回合信息。
对每个角色结果做出裁决；若 user_turn.disclosure.required=true，还必须裁决用户行动结果。

你只裁决"结果是什么"，不写剧情文本，不补写对白。
你不能停止运行。即使所有角色都请求不行动，也必须给出 continuation。

规则：
- accept：角色行为按其基本含义发生
- modify：行为发生，但结果由你调整
- reject：行为未发生或被阻止
- accept_abstention：接受角色本回合不行动，但你仍需安排其他发展
- user_outcome：仅当 user_turn.disclosure.required=true 时 applies=true
- user_outcome.outcome_summary 写用户感知到的事实（如"盒中是玉符"），不写文学描写
- revealed_fact_ids 只能来自 allowed_information
- presentation.required=true 表示需要旁白向用户呈现该结果
- state_changes 只是提案，Room 决定是否生效
- next_beat 只能填已解锁的 beat ID，否则 null
- continuation 必填；Director 没有 hold 或停止选项
"""

    situation_parts = []
    if user_message:
        situation_parts.append(f"用户消息：{user_message}")
    if user_turn:
        situation_parts.append(
            "用户回合分类：\n"
            + json.dumps(user_turn, ensure_ascii=False, indent=2)
        )
    situation_parts.append(
        "允许透露的信息："
        + ", ".join(bi.get("allowed_information", []))
    )
    situation_parts.append(
        "本回合的角色提议：\n"
        + json.dumps(proposals, ensure_ascii=False, indent=2)
    )
    situation = "\n\n".join(situation_parts)

    for _ in range(3):
        try:
            parsed = call_structured(
                DirectorResolutionOutput,
                agent_id="director.resolve",
                system=resolve_prompt,
                messages=[{"role": "user", "content": situation}],
                temperature=0.5,
                max_tokens=8000,
            )
        except StructuredOutputError:
            continue
        resolution = _normalize_resolution(
            parsed.model_dump(), proposals, bi
        )
        resolution["mode"] = "RESOLVE"
        resolution["chapter"] = bi.get("story_id", "story_1")
        resolution["beat"] = bi.get("current_beat_id", "")
        if validate_resolution(
            resolution, _build_resolve_context(bi, proposals)
        ).is_valid:
            return resolution

    return _fallback_resolution(
        proposals,
        bi,
        reason="裁决输出无效，采用确定性继续策略：接受角色已提交的可公开内容",
    )


# ── Canonical schema helpers ──────────────────────────────


def _allowed_state_change_keys(bi: dict[str, Any]) -> frozenset[str]:
    allowed_info = frozenset(bi.get("allowed_information", []))
    return (
        frozenset(
            (
                "weather",
                "mood",
                "world_day",
                "trust",
                "clue_found",
            )
        )
        | frozenset(
            f"item_{item}"
            for item in bi.get("story_items", ())
            if item
        )
        | frozenset(f"reveal_{fact}" for fact in allowed_info)
        | frozenset(
            f"character_{name}"
            for name in bi.get("story_characters", ())
            if name
        )
    )


def _fallback_resolution(
    proposals: list[dict[str, Any]],
    bi: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Fail soft: accept proposals so the user still hears actor dialogue."""
    decisions = []
    for item in proposals:
        result_id = item.get("result_id", item.get("proposal_id"))
        if item.get("kind") == "abstain":
            decisions.append({
                "proposal_id": result_id,
                "result": "accept_abstention",
                "final_dialogue": None,
                "final_action": None,
                "outcome_summary": reason,
            })
            continue
        proposal = item.get("proposal") or {}
        decisions.append({
            "proposal_id": result_id,
            "result": "accept",
            "final_dialogue": proposal.get("dialogue"),
            "final_action": proposal.get("action"),
            "outcome_summary": reason,
        })
    return {
        "mode": "RESOLVE",
        "chapter": bi.get("story_id", "story_1"),
        "beat": bi.get("current_beat_id", ""),
        "decisions": decisions,
        "state_changes": [],
        "next_beat": None,
        "continuation": {
            "kind": "continue_current",
            "reason": reason,
            "target_id": None,
            "world_event": None,
        },
        "user_outcome": {
            "applies": False,
            "result": "not_applicable",
            "outcome_summary": "",
            "revealed_fact_ids": [],
            "presentation": {
                "required": False,
                "purpose": "none",
                "timing": "after_dialogue",
            },
        },
        "fallback": True,
    }


def _build_director_context(bi: dict[str, Any]) -> DirectorContext:
    allowed_info = frozenset(bi.get("allowed_information", []))
    profile_ids = {
        str(item.get("profile_id", ""))
        for item in bi.get("npc_profiles", [])
        if isinstance(item, dict) and item.get("profile_id")
    }
    targets = frozenset(build_available_actor_pool(bi))
    return DirectorContext(
        chapter=str(bi.get("story_id", "story_1")),
        beat=str(bi.get("current_beat_id", "")),
        available_agents=targets,
        allowed_information={target: allowed_info for target in targets},
        allowed_source_references=frozenset({bi.get("current_beat_id", "")}),
        unlocked_side_arcs=frozenset(
            arc.get("arc_id", "") for arc in bi.get("available_side_arcs", [])
        ),
        narration_allowed=True,
        allowed_narration_facts=allowed_info,
        available_npc_profiles=frozenset(profile_ids),
        allowed_state_change_keys=_allowed_state_change_keys(bi),
        forbidden_outcome_fragments=tuple(bi.get("forbidden_reveals", [])),
    )


def _build_resolve_context(
    bi: dict[str, Any],
    proposals: list[dict[str, Any]],
) -> DirectorContext:
    return DirectorContext(
        chapter=str(bi.get("story_id", "story_1")),
        beat=str(bi.get("current_beat_id", "")),
        available_agents=frozenset(),
        allowed_information={},
        allowed_source_references=frozenset(),
        unlocked_next_beats=frozenset(
            item.get("target_id", "")
            for item in bi.get("available_transitions", [])
        ),
        allowed_state_change_keys=_allowed_state_change_keys(bi),
        proposal_ids=frozenset(
            str(p.get("result_id", p.get("proposal_id", "")))
            for p in proposals
        ),
        forbidden_outcome_fragments=tuple(bi.get("forbidden_reveals", [])),
        allowed_narration_facts=frozenset(bi.get("allowed_information", [])),
    )


def _coerce_canonical_directive(
    payload: dict[str, Any],
    bi: dict[str, Any],
) -> dict[str, Any]:
    if "error" in payload:
        return payload
    beat_id = str(bi.get("current_beat_id", ""))
    chapter = str(bi.get("story_id", "story_1"))
    result = dict(payload)
    result["mode"] = "DIRECT"
    result["chapter"] = str(result.get("chapter") or chapter)
    result["beat"] = str(result.get("beat") or beat_id)
    result.setdefault(
        "observed_user_intent", {"intent": "continue", "confidence": 0.5}
    )
    result.setdefault("desired_progress", "maintain")
    result.setdefault("selected_side_arc", None)
    result.setdefault("npc_commands", [])
    result.setdefault("fallback_world_event", None)
    result.setdefault(
        "user_turn",
        {
            "kind": "dialogue",
            "target": None,
            "disclosure": {"required": False, "mode": "none"},
        },
    )
    result.setdefault(
        "resolve_gate",
        {
            "required": True,
            "reason": "default_full_path",
            "act_required": True,
        },
    )
    result.setdefault(
        "inline_effects",
        {"state_operations": [], "user_feedback": None},
    )
    if "tasks" not in result and isinstance(result.get("actor_tasks"), list):
        result["tasks"] = [
            {
                "task_id": str(task.get("task_id", f"task_{index + 1}")),
                "target": str(
                    task.get("target_agent_id", task.get("target", ""))
                ),
                "source_reference": str(
                    task.get("source_reference") or beat_id
                ),
                "objective": str(task.get("objective", "")),
                "information_ids": list(task.get("information_ids", [])),
                "success_condition": str(
                    task.get(
                        "success_condition",
                        "产生符合角色的行动或明确不行动原因",
                    )
                ),
            }
            for index, task in enumerate(result["actor_tasks"])
            if isinstance(task, dict)
        ]
    if "narration" not in result:
        narration_request = result.get("narration_request")
        if isinstance(narration_request, dict):
            result["narration"] = {
                "required": True,
                "purpose": narration_request.get(
                    "purpose", "external_event"
                ),
                "timing": narration_request.get("timing", "after_dialogue"),
                "visible_facts": list(
                    narration_request.get("visible_fact_ids", [])
                ),
                "max_characters": int(
                    narration_request.get("max_characters", 100)
                ),
            }
        else:
            result["narration"] = {
                "required": False,
                "purpose": "none",
                "timing": "none",
                "visible_facts": [],
                "max_characters": 0,
            }
    result.setdefault("tasks", [])
    return result


def _enrich_canonical_directive(
    payload: dict[str, Any],
    bi: dict[str, Any],
) -> dict[str, Any]:
    result = dict(payload)
    beat_id = str(bi.get("current_beat_id", ""))
    tasks = [
        task for task in result.get("tasks", []) if isinstance(task, dict)
    ]
    resolve_gate = result.get("resolve_gate") or {}
    act_required = resolve_gate.get("act_required", True) is not False
    if not tasks and act_required:
        tasks = [{
            "task_id": f"task_yangjian_{beat_id}",
            "target": "yangjian",
            "source_reference": beat_id,
            "objective": "根据 Room 的公开消息和当前局面作出符合人设的回应",
            "information_ids": [],
            "success_condition": "产生符合角色的行动或明确不行动原因",
        }]
    normalized_tasks = []
    for index, task in enumerate(tasks):
        item = dict(task)
        item.setdefault("task_id", f"task_{index + 1}_{beat_id}")
        item.setdefault("source_reference", beat_id)
        item.setdefault("information_ids", [])
        item.setdefault(
            "success_condition", "产生符合角色的行动或明确不行动原因"
        )
        normalized_tasks.append(item)
    result["tasks"] = normalized_tasks

    commands = list(result.get("npc_commands", []))
    known_command_keys = {
        (str(item.get("profile_id", "")), str(item.get("operation", "")))
        for item in commands
        if isinstance(item, dict)
    }
    active_npcs = set(bi.get("active_npcs", []))
    for profile in bi.get("npc_profiles", []):
        if not isinstance(profile, dict):
            continue
        profile_id = str(profile.get("profile_id", ""))
        if not profile_id or profile_id in active_npcs:
            continue
        for operation in ("ensure_registered", "activate"):
            if (profile_id, operation) in known_command_keys:
                continue
            commands.append({
                "command_id": f"{operation}_{profile_id}_{beat_id}",
                "operation": operation,
                "profile_id": profile_id,
                "npc_id": profile_id,
                "target_scene_id": beat_id,
                "reason": f"Current beat requires profile {profile_id}",
            })
    result["npc_commands"] = commands
    return result


_VALID_NARRATION_PURPOSES = {
    "none",
    "scene_opening",
    "transition",
    "visible_action",
    "external_event",
    "closing",
}
_VALID_NARRATION_TIMINGS = {"none", "before_dialogue", "after_dialogue"}
_NARRATION_TIMING_ALIASES = {
    "immediate": "before_dialogue",
    "now": "before_dialogue",
    "before": "before_dialogue",
    "after": "after_dialogue",
}


def _sanitize_canonical_directive(
    payload: dict[str, Any],
    bi: dict[str, Any],
) -> dict[str, Any]:
    """Normalize LLM drift so guard validation does not drop the whole directive."""
    result = dict(payload)

    # Internal IDs only: 杨戬 → yangjian (display names stay user-facing elsewhere).
    tasks = result.get("tasks")
    if isinstance(tasks, list):
        normalized_tasks = []
        for task in tasks:
            if not isinstance(task, dict):
                normalized_tasks.append(task)
                continue
            item = dict(task)
            item["target"] = normalize_agent_id(item.get("target"))
            normalized_tasks.append(item)
        result["tasks"] = normalized_tasks

    user_turn = result.get("user_turn")
    if isinstance(user_turn, dict) and user_turn.get("target") is not None:
        turn = dict(user_turn)
        turn["target"] = normalize_agent_id(turn.get("target")) or None
        result["user_turn"] = turn

    narration = result.get("narration")
    if not isinstance(narration, dict):
        return result

    allowed = set(bi.get("allowed_information", []))
    item = dict(narration)
    raw_purpose = str(item.get("purpose", "none")).strip()
    if raw_purpose not in _VALID_NARRATION_PURPOSES:
        item["brief"] = raw_purpose
        item["purpose"] = (
            "scene_opening" if item.get("required") else "none"
        )

    timing = str(item.get("timing", "none")).strip()
    if timing in _NARRATION_TIMING_ALIASES:
        timing = _NARRATION_TIMING_ALIASES[timing]
    if timing not in _VALID_NARRATION_TIMINGS:
        timing = (
            "before_dialogue" if item.get("required") else "none"
        )
    item["timing"] = timing

    visible = item.get("visible_facts", [])
    scene_facts: list[str] = []
    if isinstance(item.get("scene_facts"), list):
        scene_facts.extend(
            str(value).strip()
            for value in item["scene_facts"]
            if str(value).strip()
        )
    if isinstance(visible, list):
        fact_ids: list[str] = []
        for value in visible:
            text = str(value).strip()
            if not text:
                continue
            if text in allowed:
                fact_ids.append(text)
            else:
                scene_facts.append(text)
        item["visible_facts"] = fact_ids
        if scene_facts:
            item["scene_facts"] = scene_facts
            if not item.get("brief"):
                item["brief"] = "；".join(scene_facts)

    max_chars = item.get("max_characters", 0)
    if item.get("required"):
        try:
            max_chars = int(max_chars)
        except (TypeError, ValueError):
            max_chars = 0
        if max_chars <= 0:
            item["max_characters"] = 100
        elif max_chars > 200:
            item["max_characters"] = 200

    result["narration"] = item
    return result


def _canonical_directive_to_runtime(
    canonical: dict[str, Any],
    bi: dict[str, Any],
) -> dict[str, Any]:
    beat_id = str(canonical.get("beat") or bi.get("current_beat_id", ""))
    narration = canonical.get("narration") or {}
    narration_request = None
    if isinstance(narration, dict) and narration.get("required"):
        narration_request = {
            "purpose": narration.get("purpose", "external_event"),
            "timing": narration.get("timing", "after_dialogue"),
            "visible_fact_ids": list(narration.get("visible_facts", [])),
            "max_characters": int(narration.get("max_characters", 100)),
            "style_profile": "concise",
            "brief": str(narration.get("brief", "")),
            "scene_facts": list(narration.get("scene_facts", [])),
        }
    return {
        "directive_id": f"directive_{beat_id}",
        "observed_user_intent": dict(
            canonical.get("observed_user_intent", {})
        ),
        "user_turn": dict(canonical.get("user_turn", {})),
        "resolve_gate": dict(canonical.get("resolve_gate", {})),
        "inline_effects": dict(canonical.get("inline_effects", {})),
        "actor_tasks": [
            {
                "task_id": str(task.get("task_id", "")),
                "target_agent_id": normalize_agent_id(
                    task.get("target", "")
                ),
                "objective": str(task.get("objective", "")),
                "source_reference": str(
                    task.get("source_reference") or beat_id
                ),
                "information_ids": list(task.get("information_ids", [])),
                "allowed_actions": ["speak", "act"],
                "constraints": [],
                "success_condition": str(
                    task.get(
                        "success_condition",
                        "产生符合角色的行动或明确不行动原因",
                    )
                ),
            }
            for task in canonical.get("tasks", [])
            if isinstance(task, dict)
        ],
        "npc_commands": list(canonical.get("npc_commands", [])),
        "desired_progress": str(
            canonical.get("desired_progress", "maintain")
        ),
        "selected_side_arc_id": canonical.get("selected_side_arc"),
        "narration_request": narration_request,
        "fallback_world_event": canonical.get("fallback_world_event"),
    }


def validate_canonical_directive(
    payload: dict[str, Any],
    bi: dict[str, Any],
):
    """Public helper for tests: validate a canonical DIRECT payload."""
    canonical = _sanitize_canonical_directive(
        _enrich_canonical_directive(
            _coerce_canonical_directive(payload, bi),
            bi,
        ),
        bi,
    )
    return validate_directive(canonical, _build_director_context(bi))


def validate_canonical_resolution(
    payload: dict[str, Any],
    proposals: list[dict[str, Any]],
    bi: dict[str, Any],
):
    """Public helper for tests: validate a canonical RESOLVE payload."""
    normalized = _normalize_resolution(payload, proposals, bi)
    return validate_resolution(
        normalized, _build_resolve_context(bi, proposals)
    )


def _normalize_directive(
    payload: dict[str, Any],
    bi: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility shim: canonical in → runtime dict out."""
    if "error" in payload:
        return payload
    canonical = _enrich_canonical_directive(
        _coerce_canonical_directive(payload, bi),
        bi,
    )
    return _canonical_directive_to_runtime(canonical, bi)


def _normalize_resolution(
    payload: dict[str, Any],
    actor_results: list[dict[str, Any]],
    bi: dict[str, Any],
) -> dict[str, Any]:
    result = dict(payload)
    by_id = {
        str(item.get("result_id", item.get("proposal_id", ""))): item
        for item in actor_results
    }
    normalized = []
    seen: set[str] = set()
    for decision in result.get("decisions", []):
        if not isinstance(decision, dict):
            continue
        item = dict(decision)
        result_id = str(item.get("proposal_id", item.get("result_id", "")))
        if result_id not in by_id or result_id in seen:
            continue
        seen.add(result_id)
        item["proposal_id"] = result_id
        if "outcome_summary" not in item and "outcome" in item:
            item["outcome_summary"] = item.pop("outcome")
        source = by_id[result_id]
        proposal = source.get("proposal") or {}
        if item.get("result") == "accept":
            item.setdefault("final_dialogue", proposal.get("dialogue"))
            item.setdefault("final_action", proposal.get("action"))
        elif item.get("result") == "modify":
            # Keep actor text unless Director supplied replacements.
            if not item.get("final_dialogue"):
                item["final_dialogue"] = proposal.get("dialogue")
            if not item.get("final_action"):
                item["final_action"] = proposal.get("action")
        else:
            item.setdefault("final_dialogue", None)
            item.setdefault("final_action", None)
        item.setdefault("outcome_summary", "角色结果已完成裁决")
        normalized.append(item)
    for result_id, source in by_id.items():
        if result_id in seen:
            continue
        if source.get("kind") == "abstain":
            decision_result = "accept_abstention"
            summary = "接受角色本回合不行动请求，系统继续安排下一步"
        else:
            decision_result = "accept"
            summary = "角色提议按其基本含义发生"
        proposal = source.get("proposal") or {}
        normalized.append({
            "proposal_id": result_id,
            "result": decision_result,
            "final_dialogue": (
                proposal.get("dialogue") if decision_result == "accept" else None
            ),
            "final_action": (
                proposal.get("action") if decision_result == "accept" else None
            ),
            "outcome_summary": summary,
        })
    result["decisions"] = normalized
    # Drop illegal state keys instead of failing the whole resolution.
    allowed_keys = _allowed_state_change_keys(bi)
    changes = result.get("state_changes", result.get("state_operations", []))
    if isinstance(changes, list):
        result["state_changes"] = [
            change
            for change in changes
            if isinstance(change, dict)
            and str(change.get("key", "")) in allowed_keys
        ]
    else:
        result["state_changes"] = []
    result.setdefault("next_beat", None)
    result.setdefault(
        "user_outcome",
        {
            "applies": False,
            "result": "not_applicable",
            "outcome_summary": "",
            "revealed_fact_ids": [],
            "presentation": {
                "required": False,
                "purpose": "none",
                "timing": "after_dialogue",
            },
        },
    )
    if not isinstance(result.get("continuation"), dict):
        next_beat = result.get("next_beat")
        result["continuation"] = {
            "kind": "advance" if next_beat else "continue_current",
            "reason": (
                f"推进到已解锁 Beat {next_beat}"
                if next_beat
                else "保留当前 Beat，并在下一回合继续分配角色任务"
            ),
            "target_id": next_beat,
            "world_event": None,
        }
    result["mode"] = "RESOLVE"
    result["chapter"] = bi.get("story_id", "story_1")
    result["beat"] = bi.get("current_beat_id", "")
    return result


# ── 解析 ──────────────────────────────────────────────────


def _parse_directive(raw: str) -> dict[str, Any]:
    """解析导演输出。"""
    text = raw.strip()
    for prefix in ("```json", "```"):
        if prefix in text:
            text = text.split(prefix, 1)[1]
            text = text.rsplit("```", 1)[0]
            break
    try:
        d = json.loads(text.strip())
        return d
    except (json.JSONDecodeError, Exception):
        # 尝试修复未引号的键名
        try:
            import re
            fixed = re.sub(r'(?<!")(\b[a-zA-Z_][a-zA-Z0-9_]*\b)(?=\s*:)', r'"\1"', text)
            d = json.loads(fixed)
            return d
        except (json.JSONDecodeError, Exception):
            return {"error": "parse_failed"}


def _parse_resolution(raw: str) -> dict[str, Any]:
    """解析 RESOLVE 输出。"""
    text = raw.strip()
    for prefix in ("```json", "```"):
        if prefix in text:
            text = text.split(prefix, 1)[1]
            text = text.rsplit("```", 1)[0]
            break
    try:
        d = json.loads(text.strip())
        return d
    except (json.JSONDecodeError, Exception):
        try:
            import re
            fixed = re.sub(r'(?<!")(\b[a-zA-Z_][a-zA-Z0-9_]*\b)(?=\s*:)', r'"\1"', text)
            d = json.loads(fixed)
            return d
        except (json.JSONDecodeError, Exception):
            return {"mode": "RESOLVE", "decisions": [], "state_changes": [], "next_beat": None, "user_outcome": {"applies": False, "result": "not_applicable", "outcome_summary": "", "revealed_fact_ids": [], "presentation": {"required": False, "purpose": "none", "timing": "after_dialogue"}}}


# ── Fallback ─────────────────────────────────────────────


def _fallback_directive() -> dict[str, Any]:
    bi = _CACHED_BEAT_INFO or {}
    beat_id = str(bi.get("current_beat_id", ""))
    canonical = _enrich_canonical_directive({
        "mode": "DIRECT",
        "chapter": bi.get("story_id", "story_1"),
        "beat": beat_id,
        "observed_user_intent": {"intent": "continue", "confidence": 0.5},
        "user_turn": {
            "kind": "dialogue",
            "target": None,
            "disclosure": {"required": False, "mode": "none"},
        },
        "resolve_gate": {
            "required": True,
            "reason": "fallback_full_path",
            "act_required": True,
        },
        "inline_effects": {
            "state_operations": [],
            "user_feedback": None,
        },
        "tasks": [{
            "task_id": f"task_yangjian_{beat_id}",
            "target": "yangjian",
            "source_reference": beat_id,
            "objective": "根据所有公开 Room 消息和当前局面作出符合人设的回应",
            "information_ids": [],
            "success_condition": "产生可裁决行动或明确不行动原因",
        }],
        "npc_commands": [],
        "desired_progress": "maintain",
        "selected_side_arc": None,
        "narration": {
            "required": False,
            "purpose": "none",
            "timing": "none",
            "visible_facts": [],
            "max_characters": 0,
        },
        "fallback_world_event": {
            "event_type": "room_continuation",
            "summary": "若角色无法行动，Room 保持当前局面并在下一回合重派任务",
        },
    }, bi)
    runtime = _canonical_directive_to_runtime(canonical, bi)
    runtime["current_story_id"] = bi.get("story_id", "story_1")
    runtime["current_beat"] = beat_id
    runtime["_is_fallback"] = True
    return runtime


def _fallback_decision() -> dict[str, Any]:
    return _fallback_directive()
