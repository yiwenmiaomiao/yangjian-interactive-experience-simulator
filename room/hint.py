"""
用户提示生成器 - 用户用 #问题# 格式提问，获取不剧透的解答。

提示是 meta 操作：
- 不推进剧情、不调 director/actor、不改变状态
- 基于当前 beat 目的 + 可用推进方向 + 已确认事实 + 关系状态
- 回答用户疑问，但不剧透（不透露 forbidden_information、不直接给答案）
- 调一次 LLM（deepseek-chat 非推理模型）生成 1-3 句解答
"""
from __future__ import annotations

import os, sys, re
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm
import story_state as ss
import runtime_context
import state_manager
from story_facts import load_facts


def parse_hint_question(user_message: str) -> str | None:
    """解析 #问题# 格式，返回两个 # 之间的用户疑问文本。

    支持：
    - #这是什么# -> "这是什么"
    - #为什么他不说话# -> "为什么他不说话"
    - 单个 # 开头但无结尾 # -> 返回 # 后面的内容（向后兼容）
    """
    if not user_message:
        return None
    text = user_message.strip()
    if not text.startswith("#"):
        return None
    # 优先匹配 #...# 格式（内容不能为空，且首尾各一个 #）
    match = re.match(r"^#(.+)#$", text)
    if match:
        inner = match.group(1).strip()
        # 确保内容本身不是以 # 开头（排除 ## 这种）
        if inner and not inner.startswith("#"):
            return inner
    # 向后兼容：单个 # 前缀，取 # 后面的内容（不能是纯 #）
    inner = text[1:].strip()
    if inner and not inner.startswith("#"):
        return inner
    return None


def is_hint_request(user_message: str) -> bool:
    """判断用户消息是否是提示请求（# 前缀或 #...# 格式）。"""
    if not user_message:
        return False
    return user_message.strip().startswith("#")


def extract_action_text(user_message: str) -> str:
    """提取 #问题# 之外的剩余文本作为行动指令。

    例如：
    - "#这是什么# 然后走近看看" -> "然后走近看看"
    - "#这是什么#然后走近看看" -> "然后走近看看"
    - "#这是什么#" -> ""
    - "#这是什么# 我走过去 #另一个问题#" -> "我走过去"
    """
    if not user_message:
        return ""
    text = user_message.strip()
    # 去掉所有 #...# 片段
    import re as _re
    cleaned = _re.sub(r"#[^#]+#", "", text).strip()
    return cleaned if cleaned else ""


def generate_hint(
    user_id: str = "default",
    thread_id: str = "default",
    user_question: str | None = None,
) -> dict:
    """生成一条提示消息，返回 room.tick 兼容的 result dict。

    如果提供了 user_question，回答用户疑问（不剧透）。
    否则回退到方向提示模式。
    不经过 Room 主循环，直接读取状态并调用 LLM。
    """
    token = runtime_context.set_identity(user_id, thread_id)
    try:
        state = ss.load_state()
        bi = ss.get_current_beat_info(state)

        # 收集上下文
        beat_id = bi.get("current_beat_id", "")
        beat_plot = bi.get("beat_plot", "")
        transitions = bi.get("available_transitions", [])
        allowed_info = bi.get("allowed_information", [])
        forbidden_information = bi.get("forbidden_information", [])

        # 推进方向
        if transitions:
            hints = []
            for t in transitions:
                target = t.get("target_id", "")
                cons = t.get("preserved_consequences", [])
                if cons:
                    hints.append(f"向「{target}」推进：{'、'.join(cons)}")
                else:
                    hints.append(f"向「{target}」推进")
            advance_text = "；".join(hints)
        else:
            advance_text = "保持当前局面"

        # 已确认事实 + 场景
        facts = load_facts()
        facts_text = _facts_summary(facts)
        scene = state_manager.load()
        scene_text = _scene_summary(scene)

        # 关系状态
        try:
            import relationship as rel_mod
            rel_text = rel_mod.get_summary_for_director()
        except Exception:
            rel_text = "未知"

        # 最近事件
        event_log = state.get("event_log", [])
        recent_events = "\n".join(f"· {e}" for e in event_log[-5:]) if event_log else "无"

        # 根据是否有用户疑问选择不同的 prompt
        has_question = bool(user_question and user_question.strip())

        if has_question:
            system = (
                "你是杨戬项目的提示系统。用户在剧情中提出了一个疑问，"
                "需要你基于当前剧情状态给出合理范围内的解答。\n"
                "规则：\n"
                "1. 直接回答用户的疑问，不要回避\n"
                "2. 只使用当前剧情中已经公开的信息（已确认事实、已发布消息）\n"
                "3. 不得剧透：不透露 forbidden_information 中的内容，不透露未来剧情走向\n"
                "4. 如果用户的疑问涉及尚未揭露的信息，告诉用户\"目前还不清楚\"或\"还需要进一步探索\"\n"
                "5. 用第二人称\"你\"视角，语气自然，像一个了解剧情的旁白在解答\n"
                "6. 一到三句话，不超过150字\n"
                "7. 不要替杨戬或其他角色说话\n"
                "8. 不要推进剧情或建议下一步行动，只回答问题本身"
            )
            prompt = (
                f"用户的疑问：{user_question}\n\n"
                f"当前剧情节点：{beat_id}\n"
                f"节点剧情：{beat_plot[:200]}\n"
                f"当前场景：\n{scene_text}\n"
                f"推进方向：{advance_text}\n"
                f"当前关系：{rel_text}\n"
                f"已确认事实：\n{facts_text[:500]}\n"
                f"最近事件：\n{recent_events}\n"
                f"禁止透露：{', '.join(forbidden_information) if forbidden_information else '无'}\n\n"
                f"请回答用户的疑问。"
            )
        else:
            system = (
                "你是杨戬项目的提示生成器。用户在剧情中暂时不知道接下来该做什么，"
                "需要你基于当前剧情状态给出一条方向提示。\n"
                "规则：\n"
                "1. 只提示方向，不直接告诉用户答案（不要说\"你需要输入X\"）\n"
                "2. 以场景/氛围暗示的方式表达，像旁白但更聚焦于\"接下来可以关注什么\"\n"
                "3. 一到两句话，不超过80字\n"
                "4. 用第二人称\"你\"视角\n"
                "5. 不要替杨戬说话，不要给出具体台词\n"
                "6. 如果当前局面确实没有明确方向，就提示用户观察环境或与角色互动"
            )
            prompt = (
                f"当前剧情节点：{beat_id}\n"
                f"节点剧情：{beat_plot[:200]}\n"
                f"推进方向：{advance_text}\n"
                f"当前场景：\n{scene_text}\n"
                f"当前关系：{rel_text}\n"
                f"已确认事实：\n{facts_text[:400]}\n"
                f"最近事件：\n{recent_events}\n\n"
                f"请生成一条提示，让用户知道接下来可以关注什么或做什么。"
            )

        raw = llm.call(
            agent_id="hint",
            system=system,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=600,
            model=os.environ.get("YANGJIAN_HINT_LLM_MODEL") or "deepseek-chat",
        )
        text = str(raw or "").strip()
        if not text or text.startswith("【"):
            if has_question:
                text = "目前还不清楚，或许再观察一下周围会找到线索。"
            else:
                text = "看看周围有什么值得注意的，或者和身边的人说说话。"
        max_len = 150 if has_question else 120
        if len(text) > max_len:
            text = text[:max_len]
        return {
            "ok": True,
            "output": [{"role": "提示", "text": text, "kind": "hint"}],
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "output": [{"role": "提示", "text": "看看周围有什么值得注意的，或者和身边的人说说话。"}],
        }
    finally:
        runtime_context.reset_identity(token)


def _scene_summary(state: dict) -> str:
    """从 world_state 提取场景摘要。"""
    scene = state.get("scene", {})
    parts = []
    for k, label in [
        ("location", "地理位置"),
        ("weather", "天气"),
        ("time_of_day", "时间"),
        ("mood", "氛围"),
    ]:
        v = scene.get(k, "")
        if v:
            parts.append(f"  {label}：{v}")
    return "\n".join(parts) if parts else "未设定"


def _facts_summary(facts: dict[str, Any]) -> str:
    parts = []
    items = facts.get("item_locations", {})
    if items:
        parts.append("物品位置：" + "、".join(f"{k}->{v}" for k, v in items.items()))
    chars = facts.get("character_states", {})
    if chars:
        parts.append("角色状态：" + "、".join(f"{k}->{v}" for k, v in chars.items()))
    revealed = facts.get("revealed_information", [])
    if revealed:
        parts.append("已揭露：" + "、".join(revealed[-3:]))
    return "\n".join(parts) or "无"
