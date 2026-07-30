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
from langfuse_logger import LangfuseCtx, log_generation, flush as lf_flush

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

SYSTEM_PROMPT = """你是杨戬 Room 的导演（织梦者）。

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
- 谁可以说话（allowed_speakers）
- 给杨戬什么信息、什么任务（局面要求，不是台词）
- 给 NPC 什么信息、什么任务
- 禁止做什么（must_not）
- 有哪些分支候选
- 推进条件
- 候选状态变化

## 任务描述规则

- task_to_yangjian 描述"杨戬面对什么局面"，不描述"杨戬说什么话"
- 例如：✅ "杨戬注意到用户对古盒的异常感兴趣，需要做出反应（可以回避、转移话题、或简短回应）"
- 例如：❌ "杨戬说：这不过是些古老的纹路"
- info_to_yangjian 是杨戬当前能感知到的信息（不是完整故事计划）
- info_to_npcs 是该 NPC 当前能看到的（不是完整故事计划）

## 输出格式

严格输出 JSON，不包含其他文字：

{
  "current_story_id": "story_1",
  "current_arc": "main / side_xxx",
  "current_beat": "m1",

  "beat_purpose": "当前 beat 的目的（从 Room 提供的上下文复制）",
  "allowed_speakers": ["旁白", "杨戬", "用户"],

  "info_to_yangjian": ["杨戬能感知到的信息"],
  "task_to_yangjian": "杨戬面对的局面要求（不规定台词）",

  "info_to_npcs": {},
  "task_to_npcs": {},

  "must_not": ["禁止做的事"],
  "branch_candidates": ["可选的分支 ID"],
  "advance_conditions": ["推进到下一 beat 的条件"],
  "state_change_candidates": []
}

## allowed_speakers 规则
- "旁白"：仅在场景转换、时间变化、必须呈现的外部事件时加入
- "杨戬"：默认加入
- "NPC_xxx"：仅当该 NPC 已被激活时加入
- "用户"：必须排在最后（每次 tick 停在用户能接上话的地方）
- 用户正在与杨戬连续对话时，不要加"旁白"
"""

# ── 故事计划上下文注入 ──────────────────────────────────

STORY_CONTEXT_TEMPLATE = """

--- Room 提供的当前上下文 ---

当前 Beat：{beat_id}
Beat 目的：{beat_purpose}

允许透露的信息：{allowed_info}
禁止透露的信息：{forbidden_reveals}

可用分支目标：{transitions}
{side_arcs_section}
已有故事事实：{consequences}
连续偏离次数：{deviation_count}
{recovery_note}

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


def decide_resolve(state, proposals: list[dict[str, Any]], user_message=None) -> dict[str, Any]:
    """故事计划模式：RESOLVE 阶段"""
    if not (_STORY_PLAN_ACTIVE and _CACHED_BEAT_INFO):
        return {"error": "no_story_context"}
    return _resolve_story(state, proposals, user_message)


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
        max_tokens=2000,
    )
    return _parse_directive(raw)


# ── 故事计划模式 ─────────────────────────────────────────


def _decide_story(state, user_message=None) -> dict[str, Any]:
    """故事计划模式：输出结构化调度指令。"""
    bi = _CACHED_BEAT_INFO
    if not bi:
        return _fallback_directive()

    side_arcs_list = [a["arc_id"] for a in bi.get("available_side_arcs", [])]
    side_text = f"可进入副线: {side_arcs_list}" if side_arcs_list else "无"
    trans_text = ", ".join(f"{t['transition_id']}->{t['target_id']}" for t in bi.get("available_transitions", []))

    user_msg_display = f"用户说：{user_message}" if user_message else "（无用户输入，系统推动）"

    context = STORY_CONTEXT_TEMPLATE.format(
        beat_id=bi.get("current_beat_id", ""),
        beat_purpose=bi.get("beat_purpose", ""),
        allowed_info=", ".join(bi.get("allowed_information", [])),
        forbidden_reveals=", ".join(bi.get("forbidden_reveals", [])),
        transitions=trans_text,
        side_arcs_section=side_text,
        consequences=", ".join(bi.get("consequences", [])),
        deviation_count=bi.get("beat_tick_counter", 0),
        recovery_note="",
        user_message_display=user_msg_display,
    )

    prompt = f"天气：{state.get('weather', '晴')} 氛围：{state.get('mood', '平静')} 第{state.get('world_day', 1)}天"

    for attempt in range(3):
        raw = llm.call(
            agent_id="director",
            system=SYSTEM_PROMPT + context,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000,
        )
        directive = _parse_directive(raw)
        if "error" not in directive:
            # 修正 beat 和 story_id
            directive["current_story_id"] = bi.get("story_id", "story_1")
            directive["current_beat"] = bi.get("current_beat_id", "")
            return directive
        if attempt < 2:
            continue

    return _fallback_directive()


def _resolve_story(state, proposals: list[dict[str, Any]], user_message=None) -> dict[str, Any]:
    """RESOLVE 阶段：裁决角色提议。"""
    bi = _CACHED_BEAT_INFO
    if not bi:
        return {"error": "no_story_context"}

    proposals_text = "\n".join(
        f" - [{p.get('npc_id', p.get('role', '?'))}] {p.get('text', '')[:80]}"
        for p in proposals
    )

    resolve_prompt = """你是导演，现在进入 RESOLVE（裁决）阶段。

你收到本回合所有角色的行动提议。对每个提议做出裁决。

你只裁决"结果是什么"，不写剧情文本，不补写对白。

输出 JSON：
{
  "mode": "RESOLVE",
  "decisions": [
    {
      "proposal_id": "角色名",
      "result": "accept / modify / reject",
      "outcome": "实际发生了什么（简短事实，不是文学描写）"
    }
  ],
  "state_changes": [
    {"key": "状态键", "value": "新值", "reason": "原因"}
  ],
  "next_beat": null
}

规则：
- accept：角色行为按其基本含义发生
- modify：行为发生，但结果由你调整
- reject：行为未发生或被阻止
- outcome 写简短事实（"杨戬回避了问题"），不写文学描写
- state_changes 只是提案，Room 决定是否生效
- next_beat 只能填已解锁的 beat ID，否则 null
"""

    situation = f"本回合的角色提议：\n{proposals_text}\n\n请裁决。"

    raw = llm.call(
        agent_id="director",
        system=resolve_prompt,
        messages=[{"role": "user", "content": situation}],
        temperature=0.5,
        max_tokens=1500,
    )

    return _parse_resolution(raw)


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
            return {"mode": "RESOLVE", "decisions": [], "state_changes": [], "next_beat": None}


# ── Fallback ─────────────────────────────────────────────


def _fallback_directive() -> dict[str, Any]:
    bi = _CACHED_BEAT_INFO or {}
    return {
        "current_story_id": bi.get("story_id", "story_1"),
        "current_arc": "main",
        "current_beat": bi.get("current_beat_id", ""),
        "beat_purpose": bi.get("beat_purpose", ""),
        "allowed_speakers": ["杨戬", "用户"],
        "info_to_yangjian": [],
        "task_to_yangjian": "回应当前局面",
        "info_to_npcs": {},
        "task_to_npcs": {},
        "must_not": [],
        "branch_candidates": [],
        "advance_conditions": [],
        "state_change_candidates": [],
    }


def _fallback_decision() -> dict[str, Any]:
    return _fallback_directive()
