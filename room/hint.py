"""
用户提示生成器 — 用户处于被动等待时，发 "#现在怎么办" 获取方向提示。

提示是 meta 操作：
- 不推进剧情、不调 director/actor、不改变状态
- 基于当前 beat 目的 + 可用推进方向 + 已确认事实 + 关系状态
- 调一次 LLM（deepseek-chat 非推理模型）生成 1-2 句方向暗示
"""
from __future__ import annotations

import os, sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm
import story_state as ss
import runtime_context
from story_facts import load_facts


def is_hint_request(user_message: str) -> bool:
    """判断用户消息是否是提示请求（# 前缀）。"""
    if not user_message:
        return False
    return user_message.strip().startswith("#")


def generate_hint(user_id: str = "default", thread_id: str = "default") -> dict:
    """生成一条提示消息，返回 room.tick 兼容的 result dict。

    不经过 Room 主循环，直接读取状态并调用 LLM。
    """
    token = runtime_context.set_identity(user_id, thread_id)
    try:
        state = ss.load_state()
        bi = ss.get_current_beat_info(state)

        # 收集上下文
        beat_id = bi.get("current_beat_id", "")
        beat_purpose = bi.get("beat_purpose", "")
        transitions = bi.get("available_transitions", [])
        allowed_info = bi.get("allowed_information", [])

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

        # 已确认事实
        facts = load_facts()
        facts_text = _facts_summary(facts)

        # 关系状态
        try:
            import relationship as rel_mod
            rel_text = rel_mod.get_summary_for_director()
        except Exception:
            rel_text = "未知"

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
            f"节点目的：{beat_purpose[:200]}\n"
            f"推进方向：{advance_text}\n"
            f"当前关系：{rel_text}\n"
            f"已确认事实：\n{facts_text[:400]}\n\n"
            f"请生成一条提示，让用户知道接下来可以关注什么或做什么。"
        )

        raw = llm.call(
            agent_id="hint",
            system=system,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=300,
            model=os.environ.get("YANGJIAN_HINT_LLM_MODEL") or "deepseek-chat",
        )
        text = str(raw or "").strip()
        if not text or text.startswith("【"):
            text = "看看周围有什么值得注意的，或者和身边的人说说话。"
        if len(text) > 120:
            text = text[:120]
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


def _facts_summary(facts: dict[str, Any]) -> str:
    parts = []
    items = facts.get("item_locations", {})
    if items:
        parts.append("物品位置：" + "、".join(f"{k}→{v}" for k, v in items.items()))
    chars = facts.get("character_states", {})
    if chars:
        parts.append("角色状态：" + "、".join(f"{k}→{v}" for k, v in chars.items()))
    revealed = facts.get("revealed_information", [])
    if revealed:
        parts.append("已揭露：" + "、".join(revealed[-3:]))
    scene = facts.get("current_scene", "")
    if scene:
        parts.insert(0, f"当前场景：{scene}")
    return "\n".join(parts) or "无"
