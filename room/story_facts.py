"""
公开事实管理器 — Room 是真相源

维护一组结构化的事实（物品位置、角色状态、已揭露信息），
所有 agent 在生成内容时必须引用。事实由 Room 统一更新，
agent 只能读取，不能修改。
"""
from __future__ import annotations

import json, os, copy, datetime
from typing import Any
import runtime_context

BASE_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))
FACTS_PATH = os.path.join(BASE_DIR, "world_facts.json")


def default_facts() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": datetime.datetime.now().isoformat(),
        # 物品位置： { "古盒": "杨戬", "三尖两刃刀": "杨戬", ... }
        "item_locations": {},
        # 角色状态： { "杨戬": "站在石台旁", "用户": "站在门口", ... }
        "character_states": {},
        # 已揭露信息： ["古盒有裂缝状符文", "符文与瑶姬封印有关", ...]
        "revealed_information": [],
        # 当前场景： "灌江口·庭院"
        "current_scene": "",
        # 氛围： "平静"
        "current_mood": "平静",
    }


def load_facts() -> dict[str, Any]:
    path = runtime_context.scoped_path(FACTS_PATH)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default_facts()


def save_facts(facts: dict[str, Any]) -> None:
    facts["version"] = facts.get("version", 0) + 1
    facts["updated_at"] = datetime.datetime.now().isoformat()
    path = runtime_context.scoped_path(FACTS_PATH)
    # 原子写入：先写临时文件再 rename，避免进程中断留下残缺 JSON
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def set_item_location(item: str, location: str) -> None:
    """设置物品位置。"""
    facts = load_facts()
    facts["item_locations"][item] = location
    save_facts(facts)


def set_character_state(character: str, state: str) -> None:
    """设置角色状态。"""
    facts = load_facts()
    facts["character_states"][character] = state
    save_facts(facts)


def reveal_information(info: str) -> None:
    """揭露一条信息（已确认的事实）。"""
    facts = load_facts()
    if info not in facts["revealed_information"]:
        facts["revealed_information"].append(info)
    save_facts(facts)


def get_facts_summary() -> str:
    """生成事实摘要（给 agent 做上下文）。"""
    facts = load_facts()
    parts = []

    # 从 world_state 读取 scene
    scene = _load_world_scene()
    if scene:
        parts.append("当前场景：")
        for k, label in [
            ("location", "  地点"),
            ("weather", "  天气"),
            ("time_of_day", "  时间"),
            ("mood", "  氛围"),
        ]:
            v = scene.get(k, "")
            if v:
                parts.append(f"{label}：{v}")

    items = facts.get("item_locations", {})
    if items:
        parts.append("物品位置：")
        for item, loc in items.items():
            parts.append(f"  {item} -> {loc}")

    chars = facts.get("character_states", {})
    if chars:
        parts.append("角色状态：")
        for char, state in chars.items():
            parts.append(f"  {char} -> {state}")

    revealed = facts.get("revealed_information", [])
    if revealed:
        parts.append(f"已揭露信息：{'、'.join(revealed[-5:])}")

    return "\n".join(parts)


def _load_world_scene() -> dict:
    """Load scene from world_state.json."""
    import runtime_context
    state_path = runtime_context.scoped_path(
        os.path.join(BASE_DIR, "world_state.json")
    )
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        return state.get("scene", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def reset_facts() -> None:
    """重置所有事实（新故事开始时）。"""
    save_facts(default_facts())
