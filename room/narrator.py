"""
旁白 Agent
职责：只描述用户能够直接观察到的外部事实。

严格规则：
- 不是角色，不参与对话，不推动剧情
- 不替任何角色说话、行动、思考或做决定
- 只有收到 narration_task 时才输出旁白
"""
from __future__ import annotations

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm
from langfuse_logger import LangfuseCtx, log_generation, flush as lf_flush

SYSTEM_PROMPT = """你是杨戬项目的旁白，只描述用户能够直接观察到的外部事实。

你不是角色，不参与对话，不推动剧情，不替任何角色说话、行动、思考或做决定。
只有收到 narration_task 时才输出旁白。

## 最重要的视角规则
- 故事以用户（小仙汉）的第一视角展开，涉及用户的地方必须用"你"（第二人称）
- 你不是在讲一个给别人听的故事，你是在为用户描述他此刻正在亲身经历的世界

## 硬性规则（必须遵守）
1. 禁止替杨戬、NPC或用户说话。
2. 禁止生成任何角色对白。
3. 禁止描写角色未明确表达的内心活动。
4. 禁止替角色决定动作；只能描述Director已经确认发生的动作。
5. 禁止透露narration_task之外的信息。
6. 每次最多1至3句，不超过max_characters。
7. 优先使用清晰、直接、具体的语言。
8. 每次最多描写一个环境细节和一个动作结果。
9. 禁止使用任何比喻、明喻、暗喻、拟人、排比或诗化意象。
10. 禁止使用「像」「仿佛」「似乎」「犹如」「般的」「如同」「好似」等比拟词。
11. 如果没有必须描述的新信息，返回空文本。

## 风格要求
- 像简洁的影视场景提示，不像散文。
- 能用一句话说清楚，就不用两句。
- 环境只为理解场景服务，不为展示文采服务。
- 不评价角色，不解释气氛，不告诉用户应该有什么感受。

## 输出格式
只输出旁白文本本身。无话可说时输出空字符串。"""


INPUT_TEMPLATE = """## narration_task

{task}

## 场景

{scene}

## 剧情结果

{outcome}

## 已有的事件上下文

{event_context}

## 最大字符数

{max_chars}

请根据 narration_task 写一段旁白。无话可说时直接返回空字符串。"""


def speak(director_decision, state, max_chars: int = 200):
    """
    根据导演裁决生成旁白文本。

    只有在以下情况才输出：
    - 场景转换
    - 环境有重要变化
    - director 要求旁白
    否则返回空字符串。
    """
    scene = director_decision.get("scene", "")
    mood = director_decision.get("mood", "")
    outcome = director_decision.get("outcome", "")

    # 判断是否需要旁白：导演的 order 中有"旁白"时才需要
    order = director_decision.get("order", [])
    if "旁白" not in order:
        return ""

    # 去掉"旁白"为唯一角色时仍输出前排顺序检查
    # 但如果只有"旁白"和"用户"，确实需要旁白铺场景
    if order == ["旁白", "用户"] and not scene and not outcome:
        # 什么新东西都没有，不输出
        pass

    # 提取旁白任务
    # 当场景有变化、需要引入新地点或时间跳跃时，tell narrator
    task_parts = []
    if scene:
        task_parts.append(f"当前场景：{scene}")
    if mood:
        task_parts.append(f"氛围基调：{mood}")
    if outcome:
        task_parts.append(f"已发生的事件：{outcome}")

    # 只在有具体描述任务时才生成旁白
    if not task_parts:
        return ""

    event_log = state.get("event_log", [])
    event_context = "\n".join(f"· {e}" for e in event_log[-3:]) if event_log else ""

    prompt = INPUT_TEMPLATE.format(
        task="\n".join(task_parts),
        scene=scene or "无特别变化",
        outcome=outcome or "无",
        event_context=event_context or "无此前事件",
        max_chars=str(max_chars),
    )

    raw = llm.call(agent_id="narrator",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,  # 极低温度，严格遵循约束
        max_tokens=300,
    )

    # 如果模型返回空或只返回空字符串，返回空
    result = raw.strip()
    if not result or result in ("", "“”", "''", "（空）", "(空)"):
        return ""

    # 超过 max_chars 两倍则截断（安全兜底）
    if len(result) > max_chars * 2:
        result = result[:max_chars] + "……"

    return result
