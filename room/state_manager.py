"""
世界状态管理器
职责：读写 world_state.json、权限过滤感知信息
"""
import json, os, copy, datetime

PROFILE_DIR = os.path.expanduser("/Users/xiaoxianhan/Documents/yangjian-room")
STATE_PATH = os.path.join(PROFILE_DIR, "world_state.json")


def load():
    """加载世界状态"""
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(state):
    """保存世界状态"""
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_perception(role_key, state, extra_context=None):
    """
    根据角色的感知权限，过滤世界信息给该角色。
    
    支持两种权限格式：
      - 扁平列表 ["weather", "time", ...]
      - dict {"can_perceive": [...]}
    """
    raw = state.get("permissions", {}).get(role_key, [])
    if isinstance(raw, dict):
        can_perceive = set(raw.get("can_perceive", []))
    else:
        can_perceive = set(raw)
    
    parts = []
    
    if "weather" in can_perceive:
        parts.append(f"天气：{state.get('weather', '未知')}")
    
    if "time" in can_perceive:
        day = state.get("world_day", 1)
        parts.append(f"第{day}天")
    
    if "ambient_events" in can_perceive:
        event_log = state.get("event_log", [])
        if event_log:
            parts.append("最近事件：")
            for e in event_log[-3:]:
                parts.append(f"  · {e}")
    
    if role_key == "yangjian":
        parts.append(f"氛围：{state.get('mood', 'neutral')}")
    
    if extra_context:
        parts.append("")
        parts.append(str(extra_context))
    
    return "\n".join(parts)


def apply_changes(state, changes):
    """
    应用导演裁决的世界变更。
    changes: dict
    """
    state = copy.deepcopy(state)
    
    if "weather" in changes:
        state["weather"] = changes["weather"]
    if "current_weather" in changes:
        state["weather"] = changes["current_weather"]
    if "mood" in changes:
        state["mood"] = changes["mood"]
    if "world_day" in changes:
        state["world_day"] = changes["world_day"]
    
    story_changes = changes.get("stories", {})
    for story_key, story_data in story_changes.items():
        if story_key not in state["stories"]:
            state["stories"][story_key] = {
                "phase": 0,
                "branch": None,
                "triggered": False,
                "completed": False,
                "completed_at": None,
                "ticks_stalled": 0,
                "ticks_since_advance": 0,
            }
        for k, v in story_data.items():
            state["stories"][story_key][k] = v
        # 自动设置 triggered 和 completed 时间
        if story_data.get("triggered") == True:
            state["stories"][story_key]["triggered"] = True
        if story_data.get("completed") == True:
            state["stories"][story_key]["completed"] = True
            state["stories"][story_key]["completed_at"] = datetime.datetime.now().isoformat()
    
    npc_changes = changes.get("npc", {})
    for npc_key, npc_data in npc_changes.items():
        if npc_key not in state.get("npc", {}):
            continue
        for k, v in npc_data.items():
            state["npc"][npc_key][k] = v
    
    events = changes.get("event_log", [])
    if not events:
        events = changes.get("public_event_log", [])
    if events:
        state.setdefault("event_log", []).extend(events)
    
    return state


def advance_time(state):
    """推进一天"""
    state["world_day"] += 1
    return state
