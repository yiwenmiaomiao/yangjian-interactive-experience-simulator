"""
杨戬 Agent
职责：以杨戬的人设和感知，回应当前事件
- 接收导演裁决 + 他感知范围内的信息
- 输出他的行动或对话
- 动作和对话必须分开发
"""
import os, json
import llm
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
