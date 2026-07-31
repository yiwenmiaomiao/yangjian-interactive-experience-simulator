"""Canonical agent IDs for internal wiring vs user-facing display names.

Internal / structured fields use English IDs (e.g. ``yangjian``).
User-visible roles and copy use Chinese names (e.g. ``杨戬``).

Each DIRECT turn also builds a dynamic *target pool* from beat context;
Director structured outputs may only select targets from that pool.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Iterable, Mapping

# Display name → canonical agent_id used in tasks / contracts / Room routing.
AGENT_ID_ALIASES: dict[str, str] = {
    "yangjian": "yangjian",
    "杨戬": "yangjian",
    "二郎神": "yangjian",
    "二郎显圣真君": "yangjian",
}

# Canonical agent_id → user-facing role / prefix label.
AGENT_DISPLAY_NAMES: dict[str, str] = {
    "yangjian": "杨戬",
}

# Per-turn pool of selectable actor ids for Director structured output.
_AVAILABLE_TARGETS: ContextVar[frozenset[str]] = ContextVar(
    "yangjian_available_targets",
    default=frozenset({"yangjian"}),
)


def normalize_agent_id(raw: str | None) -> str:
    """Map a display name or alias onto the canonical internal agent id."""
    text = str(raw or "").strip()
    if not text:
        return ""
    return AGENT_ID_ALIASES.get(text, text)


def display_agent_name(agent_id: str | None) -> str:
    """Map a canonical agent id onto the user-facing display name."""
    text = str(agent_id or "").strip()
    if not text:
        return ""
    canonical = normalize_agent_id(text)
    return AGENT_DISPLAY_NAMES.get(canonical, text)


def is_yangjian(agent_id: str | None) -> bool:
    return normalize_agent_id(agent_id) == "yangjian"


def build_available_actor_pool(bi: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Build this turn's selectable actor ids from beat / registry context."""
    data = bi or {}
    ids: set[str] = {"yangjian"}
    for item in data.get("active_npcs", ()) or ():
        nid = normalize_agent_id(str(item))
        if nid:
            ids.add(nid)
    for item in data.get("npc_profiles", ()) or ():
        if isinstance(item, Mapping) and item.get("profile_id"):
            nid = normalize_agent_id(str(item["profile_id"]))
            if nid:
                ids.add(nid)
        elif item:
            nid = normalize_agent_id(str(item))
            if nid:
                ids.add(nid)
    return tuple(sorted(ids))


def set_available_targets(targets: Iterable[str]) -> object:
    """Install the current turn's target pool for Pydantic validators."""
    pool = frozenset(
        nid
        for item in targets
        if (nid := normalize_agent_id(item))
    )
    if not pool:
        pool = frozenset({"yangjian"})
    return _AVAILABLE_TARGETS.set(pool)


def reset_available_targets(token: object) -> None:
    _AVAILABLE_TARGETS.reset(token)  # type: ignore[arg-type]


def get_available_targets() -> frozenset[str]:
    return _AVAILABLE_TARGETS.get()


def coerce_target_in_pool(raw: Any, *, allow_none: bool = False) -> str | None:
    """Normalize then require membership in the current target pool."""
    if raw is None:
        if allow_none:
            return None
        raise ValueError("target is required")
    text = str(raw).strip()
    if not text:
        if allow_none:
            return None
        raise ValueError("target is required")
    nid = normalize_agent_id(text)
    pool = get_available_targets()
    if nid not in pool:
        raise ValueError(
            f"target must be one of {sorted(pool)}, got {raw!r}"
            f" (normalized={nid!r})"
        )
    return nid


def actor_display_map(pool: Iterable[str]) -> str:
    return ", ".join(
        f"{agent_id}={display_agent_name(agent_id)}" for agent_id in pool
    )
