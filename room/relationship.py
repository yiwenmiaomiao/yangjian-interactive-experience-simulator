"""
杨戬对用户的关系状态管理。

关系是杨戬对用户的内心认知，只有杨戬自己能打分。
关系状态存在 story_state.json 中，由 Room 统一管理。

四个维度（整数）：
- trust:      -5 ~ +5，杨戬是否相信用户的动机
- respect:    -5 ~ +5，杨戬是否认可用户的品格和能力
- closeness:  -5 ~ +5，杨戬愿意让用户靠近到什么程度
- wariness:    0 ~ 5，杨戬对用户的警惕程度
"""
from __future__ import annotations

from typing import Any

import runtime_context
import story_state

_DIMENSIONS = ("trust", "respect", "closeness", "wariness")

_DEFAULTS = {
    "trust": 1,
    "respect": 0,
    "closeness": 1,
    "wariness": 1,
}

_BOUNDS = {
    "trust": (-5, 5),
    "respect": (-5, 5),
    "closeness": (-5, 5),
    "wariness": (0, 5),
}

# ── natural language descriptors per dimension ──

_DESC_TRUST = {
    -5: "你完全不信她",
    -3: "你对她的动机充满怀疑",
    -1: "你不太信她",
    0: "你对她的信任尚未建立",
    1: "你基本信她",
    3: "你信她的判断",
    5: "你无条件信任她",
}

_DESC_RESPECT = {
    -5: "你鄙视她",
    -3: "你看不起她",
    -1: "你不认可她",
    0: "你还没看清她的能力",
    1: "你觉得她还行",
    3: "你认可她的能力",
    5: "你由衷敬佩她",
}

_DESC_CLOSENESS = {
    -5: "你不想和她有任何关系",
    -3: "你刻意和她保持距离",
    -1: "你对她有些疏远",
    0: "她和普通人没什么区别",
    1: "她是你身边的人",
    3: "她是你在意的人",
    5: "她是你最亲近的人",
}

_DESC_WARINESS = {
    0: "你对她没有特别警惕",
    1: "你对她保持基本警惕",
    2: "你对她有所警惕",
    3: "你对她相当警惕",
    4: "你对她高度警惕",
    5: "你时刻提防她",
}

_DESC_MAPS = {
    "trust": _DESC_TRUST,
    "respect": _DESC_RESPECT,
    "closeness": _DESC_CLOSENESS,
    "wariness": _DESC_WARINESS,
}


def _clamp(value: int, dim: str) -> int:
    lo, hi = _BOUNDS[dim]
    return max(lo, min(hi, value))


def _nearest_key(value: int, mapping: dict[int, str]) -> str:
    """Find the nearest descriptor key for a given value."""
    keys = sorted(mapping.keys())
    best = keys[0]
    best_dist = abs(value - best)
    for k in keys[1:]:
        dist = abs(value - k)
        if dist < best_dist:
            best = k
            best_dist = dist
    return mapping[best]


def default_relationship() -> dict[str, Any]:
    return {
        "trust": _DEFAULTS["trust"],
        "respect": _DEFAULTS["respect"],
        "closeness": _DEFAULTS["closeness"],
        "wariness": _DEFAULTS["wariness"],
    }


def load_relationship() -> dict[str, Any]:
    """Load relationship from story_state.json."""
    state = story_state.load_state()
    rel = state.get("relationship")
    if not isinstance(rel, dict):
        return default_relationship()
    # Ensure all dimensions exist
    for dim in _DIMENSIONS:
        if dim not in rel:
            rel[dim] = _DEFAULTS[dim]
    return rel


def save_relationship(rel: dict[str, Any]) -> None:
    """Save relationship into story_state.json."""
    state = story_state.load_state()
    state["relationship"] = rel
    story_state.save_state(state)


def apply_delta(
    changes: dict[str, int],
    beat_id: str,
    reason: str,
) -> dict[str, Any]:
    """Apply relationship changes.

    Args:
        changes: e.g. {"trust": +1, "wariness": -1}
        beat_id: current beat where the change occurred
        reason: yangjian's internal monologue about why

    Returns:
        Updated relationship dict.
    """
    rel = load_relationship()
    clamped_changes = {}
    for dim, delta in changes.items():
        if dim not in _DIMENSIONS:
            continue
        old = rel[dim]
        new = _clamp(old + delta, dim)
        if new != old:
            rel[dim] = new
            clamped_changes[dim] = delta
    save_relationship(rel)
    return rel


def get_summary_for_yangjian() -> str:
    """Natural language summary of relationship for yangjian's perception.

    This is injected into yangjian's perception so he knows how he feels
    about the user. The language is from his perspective.
    """
    rel = load_relationship()
    lines = ["## 你对小仙汉的当前认知"]
    for dim in _DIMENSIONS:
        value = rel[dim]
        desc = _nearest_key(value, _DESC_MAPS[dim])
        lines.append(f"- {_label(dim)}：{desc}（{dim}={value}）")
    return "\n".join(lines)


def get_summary_for_director() -> str:
    """Compact summary for director's context (read-only, no scoring).

    Director needs to know the relationship to arrange reasonable situations,
    but does not score it.
    """
    rel = load_relationship()
    parts = []
    for dim in _DIMENSIONS:
        parts.append(f"{dim}={rel[dim]}")
    return "当前杨戬与用户关系：" + ", ".join(parts)


def check_requirements(requirements: dict[str, Any]) -> bool:
    """Check if current relationship meets transition requirements.

    Args:
        requirements: e.g. {"trust": {"min": 2}, "closeness": {"min": 1}}

    Returns:
        True if all requirements are met.
    """
    if not requirements:
        return True
    rel = load_relationship()
    for dim, constraint in requirements.items():
        if dim not in _DIMENSIONS:
            continue
        if not isinstance(constraint, dict):
            continue
        value = rel[dim]
        if "min" in constraint and value < constraint["min"]:
            return False
        if "max" in constraint and value > constraint["max"]:
            return False
    return True


def reset_relationship() -> dict[str, Any]:
    """Reset relationship to defaults (new story)."""
    rel = default_relationship()
    save_relationship(rel)
    return rel


def _label(dim: str) -> str:
    return {
        "trust": "信任度",
        "respect": "认可度",
        "closeness": "亲近感",
        "wariness": "警惕度",
    }.get(dim, dim)
