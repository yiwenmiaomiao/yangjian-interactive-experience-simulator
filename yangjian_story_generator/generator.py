"""Model-agnostic orchestration for story generation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .codec import story_plan_from_dict
from .models import (
    CharacterContext,
    PreferenceSnapshot,
    StoryPlan,
    StoryStandard,
)
from .ports import AsyncStructuredModelClient, StructuredModelClient
from .review_report import GeneratedPlanReviewError, ReviewReport
from .story_reviewer import review_plan
from .validation import StoryPlanValidator, ValidationReport


@dataclass(frozen=True, slots=True, kw_only=True)
class StoryBrief:
    story_id: str
    created_at: str
    premise_seed: str = ""
    preferred_themes: tuple[str, ...] = ()
    additional_constraints: tuple[str, ...] = ()


class GeneratedPlanInvalidError(ValueError):
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(
            "Generated story plan failed deterministic validation: "
            + ", ".join(issue.code for issue in report.issues)
        )


class StoryGenerator:
    """Builds a model request, parses its output, then validates the graph.

    This class is usable with a fake model client today. A real model adapter is
    deliberately absent until the Yang Jian project's model stack is available.
    """

    def __init__(
        self,
        model_client: StructuredModelClient,
        *,
        standard: StoryStandard | None = None,
    ) -> None:
        self._model_client = model_client
        self._standard = standard or StoryStandard()
        self._validator = StoryPlanValidator(self._standard)

    def generate(
        self,
        *,
        character: CharacterContext,
        preferences: PreferenceSnapshot,
        brief: StoryBrief,
        skip_review: bool = False,
    ) -> StoryPlan:
        payload = self.build_payload(
            character=character,
            preferences=preferences,
            brief=brief,
        )
        raw_plan = self._model_client.generate_story_plan(payload)
        plan = _parse_and_validate(raw_plan, self._validator)
        if not skip_review:
            _review(plan)
        return plan

    def build_payload(
        self,
        *,
        character: CharacterContext,
        preferences: PreferenceSnapshot,
        brief: StoryBrief,
    ) -> dict[str, Any]:
        """Build a deterministic, SDK-independent generation payload."""
        return _build_payload(
            character=character,
            preferences=preferences,
            brief=brief,
            standard=self._standard,
        )

    def repair_invalid_nodes(self, *_: object, **__: object) -> StoryPlan:
        # TODO(model integration):
        # Implement targeted node repair after the project's model API, prompt
        # conventions and token limits are known. It must never regenerate
        # unaffected nodes or expose private plan content in regular logs.
        raise NotImplementedError("Targeted model-based repair is not integrated")

    def generate_recovery_arc(self, *_: object, **__: object) -> object:
        # TODO(Room + model integration):
        # This requires runtime divergence context and a Room-owned rejoin
        # target. Implement only after the real Room interface is available.
        raise NotImplementedError("Recovery-arc generation is not integrated")


class AsyncStoryGenerator:
    """Async variant intended for Hermes gateway and async plugin handlers."""

    def __init__(
        self,
        model_client: AsyncStructuredModelClient,
        *,
        standard: StoryStandard | None = None,
    ) -> None:
        self._model_client = model_client
        self._standard = standard or StoryStandard()
        self._validator = StoryPlanValidator(self._standard)

    async def generate(
        self,
        *,
        character: CharacterContext,
        preferences: PreferenceSnapshot,
        brief: StoryBrief,
        skip_review: bool = False,
    ) -> StoryPlan:
        payload = self.build_payload(
            character=character,
            preferences=preferences,
            brief=brief,
        )
        raw_plan = await self._model_client.generate_story_plan(payload)
        plan = _parse_and_validate(raw_plan, self._validator)
        if not skip_review:
            _review(plan)
        return plan

    def build_payload(
        self,
        *,
        character: CharacterContext,
        preferences: PreferenceSnapshot,
        brief: StoryBrief,
    ) -> dict[str, Any]:
        return _build_payload(
            character=character,
            preferences=preferences,
            brief=brief,
            standard=self._standard,
        )


def _build_payload(
    *,
    character: CharacterContext,
    preferences: PreferenceSnapshot,
    brief: StoryBrief,
    standard: StoryStandard,
) -> dict[str, Any]:
    return {
        "task": "generate_private_branching_story_plan",
        "rules": {
            "main_focus": ["user", "yangjian"],
            "maximum_main_endings": standard.maximum_main_endings,
            "npc_only_in_side_arcs": standard.npc_only_in_side_arcs,
            "no_prebuilt_dialogue": True,
            "preserve_branch_consequences_after_reconvergence": True,
            "private_output": True,
            "scene_fields_required": [
                "world_day",
                "time_of_day",
                "weather",
                "location",
                "mood",
            ],
        },
        "brief": asdict(brief),
        "character_context": asdict(character),
        "preference_snapshot": asdict(preferences),
        "story_standard": asdict(standard),
        "output_schema": "StoryPlan",
    }


def _parse_and_validate(
    raw_plan: Any,
    validator: StoryPlanValidator,
) -> StoryPlan:
    plan = story_plan_from_dict(raw_plan)
    report = validator.validate(plan)
    if not report.is_valid:
        raise GeneratedPlanInvalidError(report)
    return plan


def _review(plan: StoryPlan) -> None:
    """Run LLM review; raises GeneratedPlanReviewError on critical errors."""
    report = review_plan(plan)
    if report.errors:
        raise GeneratedPlanReviewError(report)
