"""
杨戬 Agent
职责：以杨戬的人设和感知，回应当前事件
- 接收导演裁决 + 他感知范围内的信息
- 输出他的行动或对话
- 动作和对话必须分开发
"""
from __future__ import annotations

import os, json, re
from typing import Any
from jinja2 import Template
import llm
if __package__:
    from . import contracts
else:
    import contracts
from langfuse_logger import LangfuseCtx, log_generation, flush as lf_flush
from agent_schemas import ActorTurnOutput, StructuredOutputError, call_structured
from agent_schemas.actor import ActorAbstentionOutput

PROJECT_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))

PROMPTS_DIR = os.path.join(PROJECT_DIR, "prompts", "agents")
_TEMPLATES_DIR = os.path.join(PROJECT_DIR, "prompts", "_templates")
_SOUL_TPL: str | None = None
_TURN_TPL: str | None = None


def _load_soul() -> str:
    """加载杨戬人设（从 prompts/agents/yangjian.md 读取）"""
    global _SOUL_TPL
    if _SOUL_TPL is None:
        with open(os.path.join(PROMPTS_DIR, "yangjian.md"), "r", encoding="utf-8") as f:
            _SOUL_TPL = f.read()
    return _SOUL_TPL


def _load_turn_tpl() -> str:
    """加载 turn 输入 Jinja2 模板（从 prompts/_templates/yangjian-turn.md 读取）"""
    global _TURN_TPL
    if _TURN_TPL is None:
        with open(os.path.join(_TEMPLATES_DIR, "yangjian-turn.md"), "r", encoding="utf-8") as f:
            _TURN_TPL = f.read()
    return _TURN_TPL


# Per-story MEMORY.md 路径
def _get_memory_path():
    try:
        import story_state as ss
        story_id = getattr(ss, "_current_story_id", "story_1")
        d = os.path.join(PROJECT_DIR, "memories", story_id)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "MEMORY.md")
    except Exception:
        return os.path.join(PROJECT_DIR, "memories", "MEMORY.md")


def _format_public_message(message: Any) -> str:
    if isinstance(message, contracts.PublishedMessage):
        role = message.role
        text = message.text
    elif isinstance(message, dict):
        role = str(message.get("role", ""))
        text = str(message.get("text", ""))
    else:
        return ""
    text = text.strip()
    if not text:
        return ""
    return f"{role}：{text}"


def _summarize_history(messages: list, recent_ticks: int = 3) -> str:
    """精简公开消息：最近 N 个 tick 原文 + 之前所有 tick 的 LLM 摘要。"""
    formatted = []
    for message in messages:
        line = _format_public_message(message)
        if line:
            formatted.append((message, line))

    if not formatted:
        return "（暂无）"

    # 按 turn_id 分 tick（每个 tick 可能有多条消息）
    from collections import OrderedDict
    ticks: list[list[str]] = []
    tick_map: OrderedDict[str, list[str]] = OrderedDict()
    for _, line in formatted:
        turn_id = getattr(_, "turn_id", "") or "unknown"
        if turn_id not in tick_map:
            tick_map[turn_id] = []
        tick_map[turn_id].append(line)
    tick_groups = list(tick_map.values())

    if len(tick_groups) <= recent_ticks:
        return "\n".join(line for group in tick_groups for line in group)

    recent = tick_groups[-recent_ticks:]
    older = tick_groups[:-recent_ticks]

    # older tick 合并后 LLM 摘要
    older_text = "\n".join(
        f"[Tick {i+1}]\n" + "\n".join(group)
        for i, group in enumerate(older)
    )
    summary_prompt = (
        "以下是更早的公开消息，请用1-2句话总结发生了什么（不要列举消息数量或 tick 数量）：\n"
        + older_text
    )
    try:
        summary = llm.call(
            agent_id="yangjian",
            system="你是消息总结助手。",
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.3,
            max_tokens=200,
            model=os.environ.get("YANGJIAN_ACTOR_LLM_MODEL") or None,
        )
        summary = summary.strip() if summary else ""
    except Exception:
        raise

    parts = []
    if summary:
        parts.append(f"[前述摘要]{summary}")
    for group in recent:
        parts.extend(group)
    return "\n".join(parts)


def _build_turn_prompt(turn_input: contracts.YangJianTurnInput, *, minimal: bool = False) -> str:
    """用 Jinja2 模板渲染 turn 输入。"""
    tpl_str = _load_turn_tpl()
    if not tpl_str:
        return ""
    tpl = Template(tpl_str, keep_trailing_newline=True)

    scene = turn_input.scene or {}
    history_summary = _summarize_history(list(turn_input.public_room_history))

    return tpl.render(
        minimal=minimal,
        objective=turn_input.task.objective or "",
        beat_action_brief=turn_input.task.beat_action_brief or "",
        success_condition=turn_input.task.success_condition or "",
        scene={
            "location": scene.get("location") or "",
            "time_of_day": scene.get("time_of_day") or "",
            "weather": scene.get("weather") or "",
            "mood": scene.get("mood") or "",
        },
        history_summary=history_summary,
    )


def act_turn(turn_input: contracts.YangJianTurnInput, *, minimal: bool = False) -> dict:
    """Structured Yang Jian runtime entry point.
    If minimal=True, skips all story-mode context (task/scene/history) in the prompt.
    """
    try:
        data = call_structured(
            ActorTurnOutput,
            agent_id="yangjian",
            system=_load_soul(),
            messages=[{
                "role": "user",
                "content": _build_turn_prompt(turn_input, minimal=minimal),
            }],
            temperature=0.6,
            max_tokens=4000,
            llm_model=os.environ.get("YANGJIAN_ACTOR_LLM_MODEL") or None,
        )
    except StructuredOutputError:
        data = ActorTurnOutput(
            result_type="abstain",
            abstention=ActorAbstentionOutput(
                reason_code="INVALID_OUTPUT",
                reason="杨戬没有提出可裁决的对白或动作",
                suggested_condition=(
                    "Director should provide a concrete situation"
                ),
            ),
        )
    if data.result_type == "abstain":
        item = data.abstention
        abstention = contracts.AbstainRequest(
            request_id=f"abstain_yangjian_{turn_input.task.task_id}",
            task_id=turn_input.task.task_id,
            agent_id="yangjian",
            reason_code=str(item.reason_code if item else "INSUFFICIENT_CONTEXT"),
            reason=str(
                item.reason if item else "无法在不破坏人设的情况下行动"
            ),
            blocked_by=tuple(item.blocked_by if item else ()),
            suggested_condition=str(
                item.suggested_condition if item else ""
            ),
        )
        return contracts.to_dict(
            contracts.ActorTurnResult(
                result_id=abstention.request_id,
                task_id=turn_input.task.task_id,
                agent_id="yangjian",
                kind=contracts.ActorResultKind.ABSTAIN,
                abstention=abstention,
            )
        )

    proposal_data = data.proposal
    if proposal_data is None:
        abstention = contracts.AbstainRequest(
            request_id=f"abstain_yangjian_{turn_input.task.task_id}",
            task_id=turn_input.task.task_id,
            agent_id="yangjian",
            reason_code="EMPTY_RESPONSE",
            reason="杨戬没有提出可裁决的对白或动作",
            suggested_condition="Director should provide a concrete situation",
        )
        return contracts.to_dict(
            contracts.ActorTurnResult(
                result_id=abstention.request_id,
                task_id=turn_input.task.task_id,
                agent_id="yangjian",
                kind=contracts.ActorResultKind.ABSTAIN,
                abstention=abstention,
            )
        )
    dialogue_data = proposal_data.dialogue
    action_data = proposal_data.action
    if dialogue_data is None and action_data is None:
        abstention = contracts.AbstainRequest(
            request_id=f"abstain_yangjian_{turn_input.task.task_id}",
            task_id=turn_input.task.task_id,
            agent_id="yangjian",
            reason_code="EMPTY_RESPONSE",
            reason="杨戬没有提出可裁决的对白或动作",
            suggested_condition="Director should provide a concrete situation",
        )
        return contracts.to_dict(
            contracts.ActorTurnResult(
                result_id=abstention.request_id,
                task_id=turn_input.task.task_id,
                agent_id="yangjian",
                kind=contracts.ActorResultKind.ABSTAIN,
                abstention=abstention,
            )
        )
    proposal = contracts.ActorProposal(
        proposal_id=f"proposal_yangjian_{turn_input.task.task_id}",
        task_id=turn_input.task.task_id,
        agent_id="yangjian",
        intent=proposal_data.intent,
        dialogue=(
            contracts.DialogueProposal(
                text=dialogue_data.text,
                intent=dialogue_data.intent,
                addressee_ids=tuple(dialogue_data.addressee_ids),
            )
            if dialogue_data is not None
            else None
        ),
        action=(
            contracts.ActionProposal(
                description=action_data.description,
                action_type=action_data.action_type,
                target_ids=tuple(action_data.target_ids),
                expected_effects=tuple(action_data.expected_effects),
            )
            if action_data is not None
            else None
        ),
        proposed_effects=tuple(proposal_data.proposed_effects),
        confidence=proposal_data.confidence,
        referenced_fact_ids=tuple(proposal_data.referenced_fact_ids),
    )
    return contracts.to_dict(
        contracts.ActorTurnResult(
            result_id=proposal.proposal_id,
            task_id=turn_input.task.task_id,
            agent_id="yangjian",
            kind=contracts.ActorResultKind.PROPOSAL,
            proposal=proposal,
        )
    )


def handle_message(
    message: contracts.AgentMessage[contracts.YangJianTurnInput],
) -> contracts.AgentMessage[contracts.ActorTurnResult]:
    if message.phase is not contracts.Phase.ACT:
        raise ValueError("Yang Jian received a message outside ACT phase")
    result = contracts.actor_turn_result_from_dict(act_turn(message.payload))
    return contracts.new_message(
        turn_id=message.turn_id,
        story_id=message.story_id,
        beat_id=message.beat_id,
        phase=contracts.Phase.ACT,
        sender=message.recipient,
        recipient=message.sender,
        message_type="yangjian.turn.result",
        correlation_id=message.message_id,
        payload=result,
    )


# ── Legacy act() path (still used by room.py) ─────────────────────────────────

INPUT_TEMPLATE = """{soul}

## 你当前的感知

{perception}

## 发生的事

{event_context}

## 你本阶段的目标

{my_goal}

## 输出规则
- 如果你有动作，必须以「动作内容」的格式输出
- 如果你说话，直接输出对话
- 动作和对话必须分成不同的输出段
- 你的话很少。不是每件事都值得回应
"""


def act(director_decision, perception):
    """
    杨戬根据他感知到的信息做出回应。
    返回：{"actions": ["动作描述"], "dialogues": ["对话"]}
    """
    soul = _load_soul()

    event_context = director_decision.get("outcome", "无")
    scene = director_decision.get("scene", {})
    if isinstance(scene, dict):
        scene_str = scene.get("location", "") or str(scene)
    else:
        scene_str = str(scene)

    my_goal = "无特殊要求"
    goals = director_decision.get("goals", {})
    direct_goal = goals.get("杨戬") if isinstance(goals, dict) else None
    if isinstance(direct_goal, str) and direct_goal.strip():
        my_goal = direct_goal
    else:
        for story_goals in goals.values() if isinstance(goals, dict) else ():
            if isinstance(story_goals, dict):
                nested_goal = story_goals.get("杨戬")
                if isinstance(nested_goal, str) and nested_goal.strip():
                    my_goal = nested_goal
                    break

    context = f"场景：{scene_str}\n事件：{event_context}"

    prompt = INPUT_TEMPLATE.format(
        soul=soul,
        perception=perception,
        event_context=context,
        my_goal=my_goal,
    )

    raw = llm.call(agent_id="yangjian",
        system="你是杨戬，二郎显圣真君。",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=4000,
        model=os.environ.get("YANGJIAN_ACTOR_LLM_MODEL") or None,
    )

    return _parse_output(raw)


def _parse_output(raw):
    """解析杨戬的输出，分离动作和对话"""
    actions = []
    dialogues = []

    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("【杨戬的动作】"):
            action = line.replace("【杨戬的动作】", "").strip()
            action = action.strip("【】")
            actions.append(action)
        elif (
            (line.startswith("「") and line.endswith("」"))
            or line.startswith("（")
            or line.startswith("【")
        ):
            text = line.strip("「」【】（）")
            actions.append(text)
        else:
            dialogues.append(line)

    if not actions and not dialogues:
        dialogues.append(raw.strip())

    return {"actions": actions, "dialogues": dialogues}
