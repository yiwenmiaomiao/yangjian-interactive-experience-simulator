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
SOUL_PATH = os.path.join(PROJECT_DIR, "SOUL.md")
MEMORY_PATH = os.path.join(PROJECT_DIR, "memories", "MEMORY.md")


def _load_soul():
    """加载杨戬人设"""
    with open(SOUL_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_memory():
    """加载杨戬当前感知记忆"""
    if not os.path.exists(MEMORY_PATH):
        return "无近期记忆"
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        return f.read()


SYSTEM_PROMPT_HEAD = """你是杨戬，二郎显圣真君。"""


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


def _summarize_history(messages: list, recent_count: int = 3) -> str:
    """精简公开消息：最近 N 条原文 + 之前的摘要。"""
    formatted = []
    for message in messages:
        line = _format_public_message(message)
        if line:
            formatted.append(line)
    if len(formatted) <= recent_count:
        return "\n".join(formatted) if formatted else "（暂无）"
    recent = formatted[-recent_count:]
    older = formatted[:-recent_count]
    # 摘要：只保留角色名和消息数
    role_counts = {}
    for line in older:
        role = line.split("：")[0] if "：" in line else "未知"
        role_counts[role] = role_counts.get(role, 0) + 1
    summary = "、".join(f"{r}{c}条" for r, c in role_counts.items())
    return f"[此前消息摘要：{summary}]\n" + "\n".join(recent)


def _build_turn_prompt(turn_input: contracts.YangJianTurnInput) -> str:
    lines = [
        "## 本回合任务：",
        turn_input.task.objective,
    ]
    if turn_input.task.success_condition.strip():
        lines.append(f"成功条件：{turn_input.task.success_condition}")
    
    # 场景信息（从 beat plan 传入）
    scene = turn_input.scene or {}
    if scene:
        scene_parts = []
        if scene.get("location"):
            scene_parts.append(f"当前地点：{scene['location']}")
        if scene.get("time_of_day"):
            scene_parts.append(f"时间：{scene['time_of_day']}")
        if scene.get("weather"):
            scene_parts.append(f"天气：{scene['weather']}")
        if scene.get("mood"):
            scene_parts.append(f"氛围：{scene['mood']}")
        if scene_parts:
            lines.append("")
            lines.append("## 场景：")
            for p in scene_parts:
                lines.append(f"- {p}")
    
    if turn_input.perception:
        lines.append("")
        lines.append("## 额外感知：")
        
        date_str = ""
        weather_str = ""
        atmos_str = ""
        events_str = ""
        rel_str = ""
        others = []
        
        for fact in turn_input.perception:
            text = fact.text.strip()
            if not text:
                continue
            
            # 独立处理人物认知区块
            if "对小仙汉的当前认知" in text:
                rel_str = text if text.startswith("##") else f"## {text}"
                continue
                
            # 去除可能自带的横杠，便于重新格式化
            if text.startswith("- "):
                text = text[2:]
                
            # 分类与格式化
            if re.match(r"^第\d+天$", text):
                date_str = f"- 日期：{text}"
            elif text.startswith("天气："):
                weather_str = f"- {text}"
            elif text.startswith("氛围："):
                atmos_str = f"- {text}"
            elif text.startswith("最近事件："):
                events_str = f"- 最近事件：\n{text[5:].strip()}"
            else:
                others.append(text)
        
        # 强制按照需要的顺序渲染
        if date_str: lines.append(date_str)
        if weather_str: lines.append(weather_str)
        if atmos_str: lines.append(atmos_str)
        if events_str: lines.append(events_str)
        
        for o in others:
            if "\n" in o:
                lines.append(o)
            else:
                lines.append(f"- {o}")
                
        # 认知状态放在最后
        if rel_str:
            lines.append("")
            lines.append(rel_str)

    lines.append("")
    lines.append("## 公开消息：")
    history = list(turn_input.public_room_history)
    lines.append(_summarize_history(history))
    return "\n".join(lines)


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


def act_turn(turn_input: contracts.YangJianTurnInput) -> dict:
    """Structured Yang Jian runtime entry point."""
    try:
        data = call_structured(
            ActorTurnOutput,
            agent_id="yangjian",
            system=_load_soul(),
            messages=[{
                "role": "user",
                "content": _build_turn_prompt(turn_input),
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


def act(director_decision, perception):
    """
    杨戬根据他感知到的信息做出回应。
    返回：{"actions": ["动作描述"], "dialogues": ["对话"]}
    """
    soul = _load_soul()
    # memory = _load_memory()
    
    event_context = director_decision.get("outcome", "无")
    scene = director_decision.get("scene", {})
    if isinstance(scene, dict):
        scene_str = scene.get("location", "") or str(scene)
    else:
        scene_str = str(scene)
    
    # 提取杨戬阶段目标
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
        system=SYSTEM_PROMPT_HEAD,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=4000,
        model=os.environ.get("YANGJIAN_ACTOR_LLM_MODEL") or None,
    )
    
    return _parse_output(raw)


def _parse_json_object(raw: str) -> dict:
    text = raw.strip()
    for prefix in ("```json", "```"):
        if prefix in text:
            text = text.split(prefix, 1)[1]
            text = text.rsplit("```", 1)[0]
            break
    try:
        value = json.loads(text.strip())
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


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
            # 去掉动作内容自带的【】包裹
            action = action.strip("【】")
            actions.append(action)
        elif (
            (line.startswith("「") and line.endswith("」"))
            or line.startswith("（")
            or line.startswith("【")
        ):
            # 可能是标注，去掉外层括号
            text = line.strip("「」【】（）")
            actions.append(text)
        else:
            dialogues.append(line)
    
    if not actions and not dialogues:
        dialogues.append(raw.strip())
    
    return {"actions": actions, "dialogues": dialogues}