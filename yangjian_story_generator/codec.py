"""JSON codec and spoiler-safe public projection for story plans."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Mapping

from .models import (
    BranchTransition,
    Condition,
    ConditionOperator,
    Ending,
    MainArc,
    NarrativeFunction,
    NPCProfileSpec,
    NPCRequirement,
    RelationshipCheckpoint,
    Secret,
    SideArc,
    StoryBeat,
    StoryPlan,
)


def story_plan_to_dict(plan: StoryPlan) -> dict[str, Any]:
    """Return the complete private representation.

    Callers must not expose this result to user-facing logs or prompts.
    """

    return asdict(plan)


def story_plan_to_json(plan: StoryPlan, *, indent: int | None = 2) -> str:
    return json.dumps(
        story_plan_to_dict(plan),
        ensure_ascii=False,
        indent=indent,
    )


def story_plan_public_summary(plan: StoryPlan) -> dict[str, Any]:
    """Return metadata that is safe to expose without plot spoilers."""

    return {
        "story_id": plan.story_id,
        "created_at": plan.created_at,
        "theme": plan.theme,
        "side_arc_count": len(plan.side_arcs),
    }


def story_plan_from_json(raw: str) -> StoryPlan:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Story plan JSON root must be an object")
    return story_plan_from_dict(value)


def story_plan_from_dict(data: Mapping[str, Any]) -> StoryPlan:
    return StoryPlan(
        story_id=str(data["story_id"]),
        created_at=str(data["created_at"]),
        premise=str(data["premise"]),
        theme=str(data["theme"]),
        main_arc=_main_arc(data["main_arc"]),
        side_arcs=tuple(_side_arc(item) for item in data.get("side_arcs", ())),
        secrets=tuple(_secret(item) for item in data.get("secrets", ()) if item and item.get("secret_id") and item.get("description")),
        npc_profiles=tuple(
            _npc_profile(item) for item in data.get("npc_profiles", ())
        ),
        global_constraints=tuple(data.get("global_constraints", ())),
    )


def _condition(data: Mapping[str, Any]) -> Condition:
    if isinstance(data, str):
        return Condition(key=data, operator=ConditionOperator.EQUALS, value=True)
    return Condition(
        key=str(data["key"]),
        operator=ConditionOperator(data["operator"]),
        value=data.get("value"),
    )


def _transition(data: Mapping[str, Any]) -> BranchTransition:
    return BranchTransition(
        transition_id=str(data["transition_id"]),
        target_id=str(data["target_id"]),
        goal=str(data.get("goal", "")),
        preserved_consequences=tuple(data.get("preserved_consequences", ())),
        relationship_requirements=data.get("relationship_requirements"),
    )


def _beat(data: Mapping[str, Any]) -> StoryBeat:
    checkpoint_data = data.get("relationship_checkpoint")
    checkpoint = None
    if isinstance(checkpoint_data, Mapping) and checkpoint_data.get("description"):
        checkpoint = RelationshipCheckpoint(
            description=str(checkpoint_data["description"]),
            evaluator=str(checkpoint_data.get("evaluator", "yangjian")),
        )
    return StoryBeat(
        beat_id=str(data["beat_id"]),
        plot=str(data.get("plot", data.get("purpose", ""))),
        participants=tuple(data["participants"]),
        transitions=tuple(
            _transition(item) for item in data.get("transitions", ())
        ),
        allowed_information=tuple(data.get("allowed_information", ())),
        forbidden_information=tuple(data.get("forbidden_information", ())),
        npc_requirement_ids=tuple(data.get("npc_requirement_ids", ())),
        diversion_allowed=bool(data.get("diversion_allowed", False)),
        world_day=str(data.get("world_day", "")),
        time_of_day=str(data.get("time_of_day", "")),
        weather=str(data.get("weather", "")),
        location=str(data.get("location", "")),
        mood=str(data.get("mood", "")),
        relationship_checkpoint=checkpoint,
    )


def _ending(data: Mapping[str, Any]) -> Ending:
    return Ending(
        ending_id=str(data["ending_id"]),
        summary=str(data["summary"]),
    )


def _npc_requirement(data: Mapping[str, Any]) -> NPCRequirement:
    try:
        narrative_function = NarrativeFunction(data["narrative_function"])
    except ValueError:
        narrative_function = NarrativeFunction.CATALYST
    return NPCRequirement(
        requirement_id=str(data["requirement_id"]),
        story_id=str(data["story_id"]),
        arc_id=str(data.get("arc_id") or data.get("side_arc_id", "")),
        narrative_function=narrative_function,
        purpose=str(data.get("purpose", "")),
        npc_background=str(data.get("npc_background") or data.get("background_requirement", "")),
        relation_to_yangjian=str(data.get("relation_to_yangjian", "")),
        relation_to_user=str(data.get("relation_to_user", "")),
        current_goal=str(data.get("current_goal", "")),
        must_know=tuple(data.get("must_know", ())),
        must_not_know=tuple(data.get("must_not_know", ())),
        reusable=bool(data.get("reusable", True)),
        constraints=tuple(data.get("constraints", ())),
    )


def _npc_profile(data: Mapping[str, Any]) -> NPCProfileSpec:
    return NPCProfileSpec(
        profile_id=str(data["profile_id"]),
        requirement_id=str(data["requirement_id"]),
        narrative_function=NarrativeFunction(
            data.get("narrative_function", "catalyst")
        ),
        name=str(data["name"]),
        public_role=str(data["public_role"]),
        personality=tuple(data.get("personality", ())),
        background=str(data.get("background", "")),
        expression_style=str(data.get("expression_style", "")),
        goals=tuple(data.get("goals", ())),
        relation_to_yangjian=str(data.get("relation_to_yangjian", "")),
        relation_to_user=str(data.get("relation_to_user", "")),
        knows=tuple(data.get("knows", ())),
        must_not_know=tuple(data.get("must_not_know", ())),
        behavior_boundaries=tuple(data.get("behavior_boundaries", ())),
        memory_seed=tuple(data.get("memory_seed", ())),
        story_bindings=tuple(data.get("story_bindings", ())),
        reusable=bool(data.get("reusable", True)),
        profile_version=int(data.get("profile_version", 1)),
    )


def _main_arc(data: Mapping[str, Any]) -> MainArc:
    return MainArc(
        goal=str(data["goal"]),
        beats=tuple(_beat(item) for item in data.get("beats", ())),
        endings=tuple(_ending(item) for item in data.get("endings", ())),
    )


def _side_arc(data: Mapping[str, Any]) -> SideArc:
    return SideArc(
        arc_id=str(data["arc_id"]),
        purpose=str(data["purpose"]),
        impact_on_main_arc=tuple(data.get("impact_on_main_arc", ())),
        beats=tuple(_beat(item) for item in data.get("beats", ())),
        npc_requirements=tuple(
            _npc_requirement(item) for item in data.get("npc_requirements", ())
        ),
    )


def _secret(data: Mapping[str, Any]) -> Secret:
    return Secret(
        secret_id=str(data.get("secret_id", "")),
        description=str(data.get("description", "")),
        known_by=tuple(data.get("known_by", ())),
        reveal_conditions=tuple(
            _condition(item) for item in data.get("reveal_conditions", ())
        ),
        never_reveal_to=tuple(data.get("never_reveal_to", ())),
    )



