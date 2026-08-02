"""NPC Manager orchestration over replaceable model, storage and runtime ports."""

from __future__ import annotations

from dataclasses import replace

from .lifecycle import TransitionTrigger, transition
from .models import (
    AcceptedNPCEvent,
    DirectorTask,
    ManagerMetrics,
    NPCMemory,
    NPCProfile,
    NPCProposal,
    NPCRecord,
    NPCStatus,
    NPCTurnContext,
)
from yangjian_story_generator.models import NPCRequirement
from .permissions import (
    ProposalValidation,
    build_turn_context,
    validate_proposal,
)
from .ports import (
    AsyncNPCRuntime,
    NPCProfileGenerator,
    NPCRepository,
    NPCRuntime,
    SemanticReuseReviewer,
)
from .registry import find_reuse_candidates


class NPCNotFoundError(KeyError):
    pass


class NPCIntegrationPendingError(RuntimeError):
    pass


class NPCManager:
    def __init__(
        self,
        *,
        repository: NPCRepository,
        profile_generator: NPCProfileGenerator | None = None,
        runtime: NPCRuntime | None = None,
        async_runtime: AsyncNPCRuntime | None = None,
        semantic_reviewer: SemanticReuseReviewer | None = None,
    ) -> None:
        self._repository = repository
        self._profile_generator = profile_generator
        self._runtime = runtime
        self._async_runtime = async_runtime
        self._semantic_reviewer = semantic_reviewer
        self.metrics = ManagerMetrics()

    @property
    def repository(self) -> NPCRepository:
        return self._repository

    def register_profile(
        self,
        profile: NPCProfile,
        *,
        story_id: str,
        requirement_id: str,
    ) -> NPCRecord:
        """Register a complete Story Generator profile without invoking an LLM."""
        existing = self._repository.get(profile.npc_id)
        if existing is not None:
            if existing.profile.profile_version != profile.profile_version:
                raise ValueError(
                    f"Profile version conflict for {profile.npc_id}: "
                    f"{existing.profile.profile_version} != {profile.profile_version}"
                )
            return existing
        normalized = replace(
            profile,
            status=NPCStatus.READY,
            profile_id=profile.profile_id or profile.npc_id,
            source_requirement_ids=tuple(
                dict.fromkeys((*profile.source_requirement_ids, requirement_id))
            ),
        )
        record = NPCRecord(
            profile=normalized,
            memory=NPCMemory(
                important_events=tuple(
                    f"profile_seed:{item}" for item in normalized.memory_seed
                )
            ),
            story_ids=(story_id,),
            last_transition_reason=(
                f"Registered Story Generator profile for {requirement_id}"
            ),
        )
        self._repository.save(record)
        self.metrics.generated_count += 1
        return record

    def acquire(self, requirement: NPCRequirement) -> NPCRecord:
        candidate = self._select_reuse_candidate(requirement)
        if candidate is not None:
            record = candidate
            if record.profile.status is NPCStatus.ARCHIVED:
                record = transition(
                    record,
                    target=NPCStatus.READY,
                    trigger=TransitionTrigger.REUSE_APPROVED,
                    reason=f"Reused for requirement {requirement.requirement_id}",
                )
            record = self._apply_requirement(record, requirement)
            self._repository.save(record)
            self.metrics.reuse_count += 1
            return record

        if self._profile_generator is None:
            raise NPCIntegrationPendingError(
                "No registered profile matches the requirement; "
                "Story Generator must provide NPCProfileCatalog"
            )
        profile = self._profile_generator.generate(requirement)
        if self._repository.get(profile.npc_id) is not None:
            raise ValueError(f"Generated duplicate npc_id: {profile.npc_id}")
        profile = self._normalize_generated_profile(profile, requirement)
        record = NPCRecord(
            profile=profile,
            story_ids=(requirement.story_id,),
            last_transition_reason=(
                f"Created for requirement {requirement.requirement_id}"
            ),
        )
        self._repository.save(record)
        self.metrics.generated_count += 1
        return record

    def activate(
        self,
        npc_id: str,
        *,
        story_id: str,
        arc_id: str,
        scene_id: str,
        reason: str,
    ) -> NPCRecord:
        record = self._get(npc_id)
        trigger = (
            TransitionTrigger.REUSE_APPROVED
            if record.profile.status is NPCStatus.COMPLETED
            else TransitionTrigger.DIRECTOR_APPROVED
        )
        activated = transition(
            record,
            target=NPCStatus.ACTIVE,
            trigger=trigger,
            reason=reason,
            story_id=story_id,
            arc_id=arc_id,
            scene_id=scene_id,
        )
        self._repository.save(activated)
        self.metrics.lifecycle_transitions += 1
        return activated

    def deactivate(self, npc_id: str, *, reason: str) -> NPCRecord:
        record = self._get(npc_id)
        updated = transition(
            record,
            target=NPCStatus.INACTIVE,
            trigger=TransitionTrigger.SCENE_ENDED,
            reason=reason,
        )
        return self._save_transition(updated)

    def complete(self, npc_id: str, *, reason: str) -> NPCRecord:
        record = self._get(npc_id)
        updated = transition(
            record,
            target=NPCStatus.COMPLETED,
            trigger=TransitionTrigger.SIDE_ARC_COMPLETED,
            reason=reason,
        )
        return self._save_transition(updated)

    def archive_story(self, story_id: str, *, reason: str) -> tuple[NPCRecord, ...]:
        archived: list[NPCRecord] = []
        for record in self._repository.list_all():
            if story_id not in record.story_ids:
                continue
            updated = transition(
                record,
                target=NPCStatus.ARCHIVED,
                trigger=TransitionTrigger.MAIN_ARC_ENDED,
                reason=reason,
            )
            self._repository.save(updated)
            self.metrics.lifecycle_transitions += 1
            archived.append(updated)
        return tuple(archived)

    def prepare_turn(self, npc_id: str, task: DirectorTask) -> NPCTurnContext:
        return build_turn_context(self._get(npc_id), task)

    def request_proposal(
        self,
        npc_id: str,
        task: DirectorTask,
    ) -> tuple[NPCProposal, ProposalValidation]:
        if self._runtime is None:
            raise NPCIntegrationPendingError(
                "No NPC runtime adapter is configured"
            )
        record = self._get(npc_id)
        context = build_turn_context(record, task)
        proposal = self._runtime.run_turn(context)
        self.metrics.runtime_turns += 1
        return proposal, validate_proposal(record, task, proposal)

    async def request_proposal_async(
        self,
        npc_id: str,
        task: DirectorTask,
    ) -> tuple[NPCProposal, ProposalValidation]:
        if self._async_runtime is None:
            raise NPCIntegrationPendingError(
                "No async NPC runtime adapter is configured"
            )
        record = self._get(npc_id)
        context = build_turn_context(record, task)
        proposal = await self._async_runtime.run_turn(context)
        self.metrics.runtime_turns += 1
        return proposal, validate_proposal(record, task, proposal)

    def record_accepted_event(
        self,
        npc_id: str,
        event: AcceptedNPCEvent,
    ) -> NPCRecord:
        record = self._get(npc_id)
        forbidden = set(record.profile.must_not_know)
        learned = tuple(
            item for item in event.learned_facts if item not in forbidden
        )
        memory = NPCMemory(
            important_events=(
                *record.memory.important_events,
                f"{event.event_id}: {event.summary}",
            ),
            relation_to_yangjian=(
                *record.memory.relation_to_yangjian,
                *(
                    (event.relation_to_yangjian_update,)
                    if event.relation_to_yangjian_update
                    else ()
                ),
            ),
            relation_to_user=(
                *record.memory.relation_to_user,
                *(
                    (event.relation_to_user_update,)
                    if event.relation_to_user_update
                    else ()
                ),
            ),
            learned_facts=tuple(
                dict.fromkeys((*record.memory.learned_facts, *learned))
            ),
            unresolved_matters=(
                *record.memory.unresolved_matters,
                *((event.unresolved_matter,) if event.unresolved_matter else ()),
            ),
        )
        updated = replace(record, memory=memory)
        self._repository.save(updated)
        return updated

    def _select_reuse_candidate(
        self, requirement: NPCRequirement
    ) -> NPCRecord | None:
        candidates = find_reuse_candidates(
            requirement,
            self._repository.list_all(),
        )
        for candidate in candidates:
            if not candidate.semantic_review_recommended:
                return candidate.record
            if self._semantic_reviewer and self._semantic_reviewer.is_compatible(
                requirement=requirement,
                candidate=candidate.record,
            ):
                return candidate.record
        return None

    @staticmethod
    def _normalize_generated_profile(
        profile: NPCProfile,
        requirement: NPCRequirement,
    ) -> NPCProfile:
        forbidden = tuple(
            dict.fromkeys((*profile.must_not_know, *requirement.must_not_know))
        )
        knows = tuple(
            item
            for item in dict.fromkeys((*profile.knows, *requirement.must_know))
            if item not in set(forbidden)
        )
        return replace(
            profile,
            status=NPCStatus.READY,
            knows=knows,
            must_not_know=forbidden,
            supported_functions=tuple(
                dict.fromkeys(
                    (*profile.supported_functions, requirement.narrative_function)
                )
            ),
            reusable=profile.reusable and requirement.reusable,
            entry_condition="",
            exit_condition="",
            source_requirement_ids=tuple(
                dict.fromkeys(
                    (*profile.source_requirement_ids, requirement.requirement_id)
                )
            ),
        )

    @staticmethod
    def _apply_requirement(
        record: NPCRecord,
        requirement: NPCRequirement,
    ) -> NPCRecord:
        profile = replace(
            NPCManager._normalize_generated_profile(
                record.profile,
                requirement,
            ),
            status=record.profile.status,
        )
        story_ids = (
            record.story_ids
            if requirement.story_id in record.story_ids
            else (*record.story_ids, requirement.story_id)
        )
        return replace(
            record,
            profile=profile,
            story_ids=story_ids,
            last_transition_reason=(
                f"Matched requirement {requirement.requirement_id}"
            ),
        )

    def _save_transition(self, record: NPCRecord) -> NPCRecord:
        self._repository.save(record)
        self.metrics.lifecycle_transitions += 1
        return record

    def _get(self, npc_id: str) -> NPCRecord:
        record = self._repository.get(npc_id)
        if record is None:
            raise NPCNotFoundError(npc_id)
        return record
