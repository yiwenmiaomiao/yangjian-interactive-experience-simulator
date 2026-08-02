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
if __package__:
    from . import contracts
else:
    import contracts
from langfuse_logger import LangfuseCtx, log_generation, flush as lf_flush
from agent_schemas import NarrationOutput, StructuredOutputError, call_structured

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
12. **每次输出旁白时，必须在 location 字段填写用户当前所在地点名称**（如"灌江口"、"密室"、"桃山·山脚"）。即使用户没有移动，也填当前所在地。空文本时也尽量填 location。

## 叙述类型（narration_task 的"类型"字段决定你写什么）
- 场景：描述用户此刻看到的新环境——地点、光线、气味、声音、可见物体。用户到了一个新地方，必须告诉他周围有什么，这是他了解世界的唯一渠道
- 线索：聚焦环境中一个值得注意的细节，暗示某事但不点明。让用户注意到但留下悬念
- 氛围：用一两句渲染情绪感受，不是新信息而是感觉
- 回忆：补充用户需要但角色不会主动说的背景知识，用"你想起..."的视角
- 旁白：简洁描述已确认发生的动作结果（默认）

## 风格要求
- 像简洁的影视场景提示，不像散文。
- 能用一句话说清楚，就不用两句。
- 环境只为理解场景服务，不为展示文采服务。
- 不评价角色，不解释气氛，不告诉用户应该有什么感受。

## 输出格式
在 text 字段写旁白正文。无话可说时 text 留空。"""


INPUT_TEMPLATE = """## narration_task

{task}

## 当前场景

{scene}

## 剧情结果

{outcome}

## 已有的事件上下文

{event_context}

## 当前已确认的公共事实

{facts_summary}

## 最大字符数

{max_chars}

请根据 narration_task 写一段旁白。无话可说时直接返回空字符串。
重要：你描写的地点必须与"当前场景"中的地理位置一致，不得凭空发明新场所。
如果你在 text 中描写了一个新地点（用户进入了一个新场所），请在 location 字段填写该地点的简短名称（如"灌江口·密室"）。如果地点没有变化，location 填 null。"""


def _format_scene(scene: dict) -> str:
    """Format scene dict into human-readable string for LLM prompt."""
    if not isinstance(scene, dict):
        return str(scene) if scene else "未设定"
    location = scene.get("location", "")
    weather = scene.get("weather", "")
    time_of_day = scene.get("time_of_day", "")
    mood = scene.get("mood", "")
    parts = []
    if location:
        parts.append(f"地理位置：{location}")
    if weather:
        parts.append(f"天气：{weather}")
    if time_of_day:
        parts.append(f"时间：{time_of_day}")
    if mood:
        parts.append(f"氛围：{mood}")
    return "\n".join(parts) if parts else "未设定"


def _build_narration_task(request: contracts.NarrationRequest) -> str:
    parts = [
        f"类型：{request.narration_type}",
        f"时机：{request.timing}",
    ]
    if request.brief.strip():
        parts.append(f"导演说明：{request.brief.strip()}")
    if request.scene_facts:
        parts.append(
            "需呈现的场景事实："
            + "；".join(str(item) for item in request.scene_facts)
        )
    return "；".join(parts)


def draft(turn_input: contracts.NarratorInput) -> dict:
    """Generate a structured draft from confirmed events only."""
    request = turn_input.narration_request
    confirmed = [
        str(event.get("summary", ""))
        for event in turn_input.confirmed_events
        if str(event.get("summary", "")).strip()
    ]
    if not confirmed:
        return contracts.to_dict(
            contracts.NarrationDraft(
                narration_id="narration_empty",
                text="",
            )
        )
    prompt = INPUT_TEMPLATE.format(
        task=_build_narration_task(request),
        scene=_format_scene(turn_input.scene),
        outcome="\n".join(confirmed),
        event_context="\n".join(
            f"{message.role}: {message.text}"
            for message in turn_input.previous_published_messages[-5:]
        ) or "无此前公开消息",
        facts_summary="\n".join(
            f"{fact.fact_id}: {fact.text}" for fact in turn_input.visible_facts
        ) or "无",
        max_chars=str(request.max_characters),
    )
    try:
        output = call_structured(
            NarrationOutput,
            agent_id="narrator",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=int(os.environ.get("YANGJIAN_NARRATOR_MAX_TOKENS", "8000")),
            llm_model=os.environ.get("YANGJIAN_NARRATOR_LLM_MODEL") or None,
        )
        text = output.text.strip()
        narration_location = getattr(output, "location", None)
    except StructuredOutputError:
        text = ""
        narration_location = None
    if text in ("", "“”", "''", "（空）", "(空)"):
        text = ""
    text = text[: request.max_characters]
    contains_dialogue = any(
        marker in text
        for marker in ("杨戬说", "用户说", "NPC说", "：“", ": “")
    )
    result = contracts.to_dict(
        contracts.NarrationDraft(
            narration_id=f"narration_{len(confirmed)}",
            text=text,
            referenced_event_ids=tuple(
                str(event.get("event_id", ""))
                for event in turn_input.confirmed_events
                if event.get("event_id")
            ),
            referenced_fact_ids=tuple(
                fact.fact_id for fact in turn_input.visible_facts
            ),
            contains_dialogue=contains_dialogue,
        )
    )
    if narration_location:
        result["location"] = narration_location
    return result


def handle_message(
    message: contracts.AgentMessage[contracts.NarratorInput],
) -> contracts.AgentMessage[contracts.NarrationDraft]:
    if message.phase is not contracts.Phase.NARRATE:
        raise ValueError("Narrator received a message outside NARRATE phase")
    raw = draft(message.payload)
    payload = contracts.NarrationDraft(
        narration_id=str(raw["narration_id"]),
        text=str(raw["text"]),
        referenced_event_ids=tuple(raw.get("referenced_event_ids", ())),
        referenced_fact_ids=tuple(raw.get("referenced_fact_ids", ())),
        contains_dialogue=bool(raw.get("contains_dialogue", False)),
    )
    return contracts.new_message(
        turn_id=message.turn_id,
        story_id=message.story_id,
        beat_id=message.beat_id,
        phase=contracts.Phase.NARRATE,
        sender=message.recipient,
        recipient=message.sender,
        message_type="narrator.draft",
        correlation_id=message.message_id,
        payload=payload,
    )


def speak(director_decision, state, max_chars: int = 200):
    """
    根据导演裁决生成旁白文本。

    只有在以下情况才输出：
    - 场景转换
    - 环境有重要变化
    - director 要求旁白
    否则返回空字符串。
    """
    scene = director_decision.get("scene", {})
    mood = director_decision.get("mood", "")
    outcome = director_decision.get("outcome", "")
    facts_summary = director_decision.get("facts_summary", "")

    # 判断是否需要旁白：导演的 order 中有"旁白"时才需要
    order = director_decision.get("order", [])
    if "旁白" not in order:
        return ""

    # 去掉"旁白"为唯一角色时仍输出前排顺序检查
    # 但如果只有"旁白"和"用户"，确实需要旁白铺场景
    scene_location = scene.get("location", "") if isinstance(scene, dict) else str(scene)
    if order == ["旁白", "用户"] and not scene_location and not outcome:
        # 什么新东西都没有，不输出
        pass

    # 提取旁白任务
    # 当场景有变化、需要引入新地点或时间跳跃时，tell narrator
    task_parts = []
    if scene_location:
        task_parts.append(f"当前场景：{_format_scene(scene) if isinstance(scene, dict) else scene_location}")
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
        scene=_format_scene(scene) if isinstance(scene, dict) else str(scene) if scene else "无特别变化",
        outcome=outcome or "无",
        event_context=event_context or "无此前事件",
        facts_summary=facts_summary or "无",
        max_chars=str(max_chars),
    )

    raw = llm.call(agent_id="narrator",
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,  # 极低温度，严格遵循约束
        max_tokens=int(os.environ.get("YANGJIAN_NARRATOR_MAX_TOKENS", "8000")),
        model=os.environ.get("YANGJIAN_NARRATOR_LLM_MODEL") or None,
    )

    # 如果模型返回空或只返回空字符串，返回空
    result = raw.strip()
    if not result or result in ("", "“”", "''", "（空）", "(空)"):
        return ""

    if len(result) > max_chars:
        result = result[:max_chars]

    return result
