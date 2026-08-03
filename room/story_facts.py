"""
公开事实管理器 — Room 是真相源

维护一组结构化的事实（物品位置、角色状态、已揭露信息），
所有 agent 在生成内容时必须引用。事实由 Room 统一更新，
agent 只能读取，不能修改。

路径：contexts/<story_id>_facts.json（与 world_state / story_state 同目录）
"""
from __future__ import annotations

import json, os, datetime
from typing import Any

import runtime_context
import state_manager as sm

# 与 state_manager 保持同目录
CONTEXTS_DIR = sm.CONTEXTS_DIR


def _current_sid() -> str:
    """获取当前 story_id，与 state_manager._current_world_path 同模式。"""
    try:
        import story_state as ss
        return ss._current_story_id
    except Exception:
        return "story_1"


def _facts_path(story_id: str) -> str:
    return os.path.join(CONTEXTS_DIR, f"{story_id}_facts.json")


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


def load_facts(story_id: str | None = None) -> dict[str, Any]:
    """加载 facts，默认为当前 story。"""
    sid = story_id or _current_sid()
    path = runtime_context.scoped_path(_facts_path(sid))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default_facts()


def save_facts(facts: dict[str, Any], story_id: str | None = None) -> None:
    """保存 facts 到对应 story 目录。"""
    sid = story_id or _current_sid()
    facts["version"] = facts.get("version", 0) + 1
    facts["updated_at"] = datetime.datetime.now().isoformat()
    path = runtime_context.scoped_path(_facts_path(sid))
    # 原子写入
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def set_item_location(item: str, location: str, story_id: str | None = None) -> None:
    """设置物品位置。"""
    facts = load_facts(story_id)
    facts["item_locations"][item] = location
    save_facts(facts, story_id)


def set_character_state(character: str, state: str, story_id: str | None = None) -> None:
    """设置角色状态。"""
    facts = load_facts(story_id)
    facts["character_states"][character] = state
    save_facts(facts, story_id)


def reveal_information(info: str, story_id: str | None = None) -> None:
    """揭露一条信息（已确认的事实）。"""
    facts = load_facts(story_id)
    if info not in facts["revealed_information"]:
        facts["revealed_information"].append(info)
    save_facts(facts, story_id)


def get_facts_summary(story_id: str | None = None) -> str:
    """生成事实摘要（给 agent 做上下文）。"""
    facts = load_facts(story_id)
    parts = []

    # 从 world_state 读取 scene
    scene = _load_world_scene(story_id)
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
        for char, st in chars.items():
            parts.append(f"  {char} -> {st}")

    revealed = facts.get("revealed_information", [])
    if revealed:
        parts.append(f"已揭露信息：{'、'.join(revealed[-5:])}")

    return "\n".join(parts)


def _load_world_scene(story_id: str | None = None) -> dict:
    """从 world_state.json 读取 scene。"""
    try:
        state = sm.load(story_id)
        return state.get("scene", {}) or {}
    except Exception:
        return {}


def reset_facts(story_id: str | None = None) -> None:
    """重置 facts（新故事开始时）。"""
    save_facts(default_facts(), story_id)
