"""Integration boundaries intentionally left without concrete adapters."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from .models import StoryPlan


class StructuredModelClient(Protocol):
    def generate_story_plan(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Generate a structured plan matching the package schema.

        TODO(project integration):
        - Implement this adapter with the model SDK used by the Yang Jian project.
        - Enforce structured output / JSON schema at the SDK boundary.
        - Add timeout, retry, token accounting and redacted logging.
        """


class AsyncStructuredModelClient(Protocol):
    async def generate_story_plan(
        self,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Async structured generation for Hermes gateway/plugin code.

        TODO(Hermes integration):
        - Implement with ``await ctx.llm.acomplete_structured(...)``.
        - Return ``result.parsed`` and fail closed when it is ``None``.
        """


class StoryPlanRepository(Protocol):
    def save_private(self, plan: StoryPlan) -> None:
        """Persist a private plan.

        TODO(project integration):
        - Implement using the project's actual database or private file store.
        - Ensure the complete plan never enters user-facing logs or prompts.
        """

    def load_private(self, story_id: str) -> StoryPlan | None:
        """Load a private plan by id."""


class CanonicalSourceLoader(Protocol):
    def load_yangjian_sources(self) -> tuple[str, ...]:
        """Load canonical Yang Jian stories and SOUL material.

        TODO(project integration):
        - Map this to the real SOUL and canonical-story locations.
        - Do not use incidental runtime AI dialogue as canonical character input.
        """
