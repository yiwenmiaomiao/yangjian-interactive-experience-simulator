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
    Foreshadowing,
    MainArc,
    NarrativeFunction,
    NPCProfileSpec,
    NPCRequirement,
    RelationshipCheckpoint,
    Secret,
    SideArc,
    StoryBeat,
    StoryPlan,
    UnlockRule,
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
        "schema_version": plan.schema_version,
        "created_at": plan.created_at,
        "theme": plan.theme,
        "main_arc_id": plan.main_arc.arc_id,
        "side_arc_count": len(plan.side_arcs),
        "character_snapshot_version": plan.character_snapshot_version,
        "preference_snapshot_version": plan.preference_snapshot_version,
        "story_standard_version": plan.story_standard_version,
    }


def story_plan_from_json(raw: str) -> StoryPlan:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Story plan JSON root must be an object")
    return story_plan_from_dict(value)


def story_plan_from_dict(data: Mapping[str, Any]) -> StoryPlan:
    return StoryPlan(
        story_id=str(data["story_id"]),
        schema_version=int(data["schema_version"]),
        created_at=str(data["created_at"]),
        character_snapshot_version=str(data["character_snapshot_version"]),
        preference_snapshot_version=int(data["preference_snapshot_version"]),
        story_standard_version=int(data["story_standard_version"]),
        premise=str(data["premise"]),
        theme=str(data["theme"]),
        main_arc=_main_arc(data["main_arc"]),
        side_arcs=tuple(_side_arc(item) for item in data.get("side_arcs", ())),
        secrets=tuple(_secret(item) for item in data.get("secrets", ()) if item and item.get("secret_id") and item.get("description")),
        foreshadowing=tuple(
            _foreshadowing(item) for item in data.get("foreshadowing", ())
            if item and item.get("foreshadowing_id") and item.get("setup_beat_id") and item.get("payoff_beat_id")
        ),
        npc_profiles=tuple(
            _npc_profile(item) for item in data.get("npc_profiles", ())
        ),
        global_constraints=tuple(data.get("global_constraints", ())),
        forbidden_reveals=tuple(data.get("forbidden_reveals", ())),
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
        conditions=tuple(_condition(item) for item in data.get("conditions", ())),
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
        purpose=str(data["purpose"]),
        goal=str(data.get("goal", "")),
        max_turns=int(data.get("max_turns", 6)),
        participants=tuple(data["participants"]),
        transitions=tuple(
            _transition(item) for item in data.get("transitions", ())
        ),
        prerequisites=tuple(data.get("prerequisites", ())),
        allowed_information=tuple(data.get("allowed_information", ())),
        forbidden_reveals=tuple(data.get("forbidden_reveals", ())),
        npc_requirement_ids=tuple(data.get("npc_requirement_ids", ())),
        reconverges_at=data.get("reconverges_at"),
        relationship_checkpoint=checkpoint,
    )


def _ending(data: Mapping[str, Any]) -> Ending:
    return Ending(
        ending_id=str(data["ending_id"]),
        summary=str(data["summary"]),
        conditions=tuple(_condition(item) for item in data.get("conditions", ())),
    )


def _npc_requirement(data: Mapping[str, Any]) -> NPCRequirement:
    try:
        narrative_function = NarrativeFunction(data["narrative_function"])
    except ValueError:
        narrative_function = NarrativeFunction.CATALYST
    return NPCRequirement(
        requirement_id=str(data["requirement_id"]),
        story_id=str(data["story_id"]),
        side_arc_id=str(data["side_arc_id"]),
        narrative_function=narrative_function,
        purpose=str(data["purpose"]),
        background_requirement=str(data.get("background_requirement", "")),
        relation_to_yangjian=str(data.get("relation_to_yangjian", "")),
        relation_to_user=str(data.get("relation_to_user", "")),
        current_goal=str(data.get("current_goal", "")),
        must_know=tuple(data.get("must_know", ())),
        must_not_know=tuple(data.get("must_not_know", ())),
        entry_condition=str(data.get("entry_condition", "")),
        exit_condition=str(data.get("exit_condition", "")),
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


def _unlock(data: Mapping[str, Any]) -> UnlockRule:
    return UnlockRule(
        minimum_main_progress=float(data.get("minimum_main_progress", 0.0)),
        required_milestones=tuple(data.get("required_milestones", ())),
        required_flags=tuple(data.get("required_flags", ())),
    )


def _main_arc(data: Mapping[str, Any]) -> MainArc:
    return MainArc(
        arc_id=str(data["arc_id"]),
        goal=str(data["goal"]),
        start_beat_id=str(data["start_beat_id"]),
        beats=tuple(_beat(item) for item in data.get("beats", ())),
        endings=tuple(_ending(item) for item in data.get("endings", ())),
        milestones=tuple(data.get("milestones", ())),
    )


def _side_arc(data: Mapping[str, Any]) -> SideArc:
    return SideArc(
        arc_id=str(data["arc_id"]),
        purpose=str(data["purpose"]),
        impact_on_main_arc=tuple(data.get("impact_on_main_arc", ())),
        unlock=_unlock(data.get("unlock", {})),
        start_beat_id=str(data["start_beat_id"]),
        beats=tuple(_beat(item) for item in data.get("beats", ())),
        endings=tuple(_ending(item) for item in data.get("endings", ())),
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


def _foreshadowing(data: Mapping[str, Any]) -> Foreshadowing:
    return Foreshadowing(
        foreshadowing_id=str(data.get("foreshadowing_id", "")),
        setup_beat_id=str(data.get("setup_beat_id", "")),
        payoff_beat_id=str(data.get("payoff_beat_id", "")),
        private_meaning=str(data.get("private_meaning", "")),
    )
