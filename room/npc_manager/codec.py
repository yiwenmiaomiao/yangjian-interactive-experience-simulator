"""JSON codec for repository adapters and migration."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Mapping

from .models import (
    NPCMemory,
    NPCProfile,
    NPCRecord,
    NPCStatus,
    NarrativeFunction,
)


def npc_record_to_json(record: NPCRecord, *, indent: int | None = 2) -> str:
    return json.dumps(asdict(record), ensure_ascii=False, indent=indent)


def npc_record_from_json(raw: str) -> NPCRecord:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("NPC record JSON root must be an object")
    return npc_record_from_dict(value)


def npc_record_from_dict(data: Mapping[str, Any]) -> NPCRecord:
    profile_data = data["profile"]
    memory_data = data.get("memory", {})
    profile = NPCProfile(
        npc_id=str(profile_data["npc_id"]),
        status=NPCStatus(profile_data["status"]),
        name=str(profile_data["name"]),
        public_role=str(profile_data["public_role"]),
        short_background=str(profile_data["short_background"]),
        current_goal=str(profile_data["current_goal"]),
        relation_to_yangjian=str(profile_data["relation_to_yangjian"]),
        relation_to_user=str(profile_data["relation_to_user"]),
        expression_style=str(profile_data["expression_style"]),
        profile_id=str(profile_data.get("profile_id", profile_data["npc_id"])),
        personality=tuple(profile_data.get("personality", ())),
        goals=tuple(profile_data.get("goals", ())),
        behavior_boundaries=tuple(
            profile_data.get("behavior_boundaries", ())
        ),
        memory_seed=tuple(profile_data.get("memory_seed", ())),
        story_bindings=tuple(profile_data.get("story_bindings", ())),
        knows=tuple(profile_data.get("knows", ())),
        must_not_know=tuple(profile_data.get("must_not_know", ())),
        supported_functions=tuple(
            NarrativeFunction(item)
            for item in profile_data.get("supported_functions", ())
        ),
        reusable=bool(profile_data.get("reusable", True)),
        permanently_unavailable=bool(
            profile_data.get("permanently_unavailable", False)
        ),
        entry_condition=str(profile_data.get("entry_condition", "")),
        exit_condition=str(profile_data.get("exit_condition", "")),
        source_requirement_ids=tuple(
            profile_data.get("source_requirement_ids", ())
        ),
        profile_version=int(profile_data.get("profile_version", 1)),
    )
    memory = NPCMemory(
        important_events=tuple(memory_data.get("important_events", ())),
        relation_to_yangjian=tuple(
            memory_data.get("relation_to_yangjian", ())
        ),
        relation_to_user=tuple(memory_data.get("relation_to_user", ())),
        learned_facts=tuple(memory_data.get("learned_facts", ())),
        unresolved_matters=tuple(memory_data.get("unresolved_matters", ())),
    )
    return NPCRecord(
        profile=profile,
        memory=memory,
        story_ids=tuple(data.get("story_ids", ())),
        active_story_id=data.get("active_story_id"),
        active_side_arc_id=data.get("active_side_arc_id"),
        active_scene_id=data.get("active_scene_id"),
        last_transition_reason=str(data.get("last_transition_reason", "")),
    )
