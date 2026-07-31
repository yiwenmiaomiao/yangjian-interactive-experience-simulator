"""
杨戬 Agent
职责：以杨戬的人设和感知，回应当前事件
- 接收导演裁决 + 他感知范围内的信息
- 输出他的行动或对话
- 动作和对话必须分开发
"""
import os, json
import llm
if __package__:
    from . import contracts
else:
    import contracts
from langfuse_logger import LangfuseCtx, log_generation, flush as lf_flush

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

STRUCTURED_SYSTEM_PROMPT = """你是杨戬。你会收到一个结构化回合输入。
你可以看到 public_room_history 中 Room 已公开的全部消息，包括用户、NPC、旁白和你自己的消息。
perception 是你额外可感知但不一定公开的事实。

只输出 JSON，不使用 Markdown。二选一：
1. 正常行动：
{"result_type":"proposal","proposal":{"intent":"意图","dialogue":{"text":"对白","intent":"表达意图","addressee_ids":[]}或null,"action":{"description":"动作","action_type":"act","target_ids":[],"expected_effects":[]}或null,"proposed_effects":[],"confidence":0.5,"referenced_fact_ids":[]}}
2. 确实无法合理行动：
{"result_type":"abstain","abstention":{"reason_code":"原因代码","reason":"具体原因","blocked_by":[],"suggested_condition":"什么条件下可以行动"}}

不行动只是请求，Director 会裁决。不要输出“沉默”“站着不动”来伪装行动。
不要宣布动作成功，不要修改 Room 状态，不要替其他角色说话。"""


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
    payload = {
        "task": contracts.to_dict(turn_input.task),
        "scene": dict(turn_input.scene),
        "public_room_history": contracts.to_dict(
            turn_input.public_room_history
        ),
        "perception": contracts.to_dict(turn_input.perception),
        "recent_memory": list(turn_input.recent_memory),
        "relationship_state": dict(turn_input.relationship_state),
        "current_stance": turn_input.current_stance,
        "character_id": turn_input.character_id,
        "soul_version": turn_input.soul_version,
    }
    raw = llm.call(
        agent_id="yangjian",
        system=f"{STRUCTURED_SYSTEM_PROMPT}\n\n## SOUL\n{_load_soul()}",
        messages=[{
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        }],
        temperature=0.6,
        max_tokens=1500,
    )
    data = _parse_json_object(raw)
    if data.get("result_type") == "abstain":
        item = data.get("abstention", {})
        abstention = contracts.AbstainRequest(
            request_id=str(
                item.get("request_id")
                or f"abstain_yangjian_{turn_input.task.task_id}"
            ),
            task_id=turn_input.task.task_id,
            agent_id="yangjian",
            reason_code=str(item.get("reason_code") or "INSUFFICIENT_CONTEXT"),
            reason=str(item.get("reason") or "无法在不破坏人设的情况下行动"),
            blocked_by=tuple(item.get("blocked_by", ())),
            suggested_condition=str(item.get("suggested_condition", "")),
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

    proposal_data = (
        data.get("proposal", {})
        if data.get("result_type") == "proposal"
        else {}
    )
    if not proposal_data:
        legacy = _parse_output(raw)
        proposal_data = {
            "intent": "respond",
            "dialogue": (
                {"text": "\n".join(legacy["dialogues"])}
                if legacy["dialogues"]
                else None
            ),
            "action": (
                {"description": "\n".join(legacy["actions"])}
                if legacy["actions"]
                else None
            ),
        }
    dialogue_data = proposal_data.get("dialogue")
    action_data = proposal_data.get("action")
    if not dialogue_data and not action_data:
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
        proposal_id=str(
            proposal_data.get("proposal_id")
            or f"proposal_yangjian_{turn_input.task.task_id}"
        ),
        task_id=turn_input.task.task_id,
        agent_id="yangjian",
        intent=str(proposal_data.get("intent") or "respond"),
        dialogue=(
            contracts.DialogueProposal(
                text=str(dialogue_data.get("text", "")),
                intent=str(dialogue_data.get("intent", "")),
                addressee_ids=tuple(dialogue_data.get("addressee_ids", ())),
            )
            if isinstance(dialogue_data, dict) and dialogue_data.get("text")
            else None
        ),
        action=(
            contracts.ActionProposal(
                description=str(action_data.get("description", "")),
                action_type=str(action_data.get("action_type", "act")),
                target_ids=tuple(action_data.get("target_ids", ())),
                expected_effects=tuple(
                    action_data.get("expected_effects", ())
                ),
            )
            if isinstance(action_data, dict) and action_data.get("description")
            else None
        ),
        proposed_effects=tuple(proposal_data.get("proposed_effects", ())),
        confidence=float(proposal_data.get("confidence", 0.5)),
        referenced_fact_ids=tuple(
            proposal_data.get("referenced_fact_ids", ())
        ),
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
    scene = director_decision.get("scene", "")
    
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
    
    context = f"场景：{scene}\n事件：{event_context}"
    
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
        max_tokens=1500,
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
