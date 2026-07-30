from __future__ import annotations

import unittest
from dataclasses import replace

from room.npc_manager import (
    AcceptedNPCEvent,
    DirectorTask,
    InMemoryNPCRepository,
    InvalidLifecycleTransition,
    NarrativeFunction,
    NPCManager,
    NPCProfile,
    NPCProposal,
    NPCRecord,
    NPCRequirement,
    NPCStatus,
    NPC_BASE_SYSTEM_PROMPT,
    NPC_PROPOSAL_SCHEMA,
    TaskSource,
    TransitionTrigger,
    build_turn_context,
    build_npc_turn_input,
    build_npc_turn_input_json,
    find_reuse_candidates,
    npc_record_from_json,
    npc_record_to_json,
    transition,
    validate_proposal,
)


def requirement(*, forbidden: tuple[str, ...] = ("main_ending",)) -> NPCRequirement:
    return NPCRequirement(
        requirement_id="req_1",
        story_id="story_1",
        side_arc_id="side_1",
        narrative_function=NarrativeFunction.CATALYST,
        purpose="Push a side arc.",
        background_requirement="Village messenger",
        relation_to_yangjian="Acquaintance",
        relation_to_user="Stranger",
        current_goal="Deliver a request",
        must_know=("public_request",),
        must_not_know=forbidden,
        entry_condition="side_1_started",
        exit_condition="request_resolved",
    )


def profile(
    *,
    npc_id: str = "npc_1",
    status: NPCStatus = NPCStatus.READY,
    knows: tuple[str, ...] = (),
) -> NPCProfile:
    return NPCProfile(
        npc_id=npc_id,
        status=status,
        name="Messenger",
        public_role="Village messenger",
        short_background="Village messenger",
        current_goal="Deliver a request",
        relation_to_yangjian="Acquaintance",
        relation_to_user="Stranger",
        expression_style="Brief and direct",
        knows=knows,
        supported_functions=(NarrativeFunction.CATALYST,),
    )


class FakeProfileGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, npc_requirement: NPCRequirement) -> NPCProfile:
        self.calls += 1
        return profile(npc_id=f"generated_{self.calls}")


class FakeRuntime:
    def __init__(self) -> None:
        self.turns: list[str] = []

    def run_turn(self, context):
        self.turns.append(context.npc_id)
        return NPCProposal(
            npc_id=context.npc_id,
            intent="deliver_request",
            utterance="I have a request.",
        )


class FakeAsyncRuntime:
    async def run_turn(self, context):
        return NPCProposal(
            npc_id=context.npc_id,
            intent="deliver_request",
            utterance="I have a request.",
        )


class NPCManagerTests(unittest.TestCase):
    def test_generates_when_no_reusable_npc_exists(self) -> None:
        repository = InMemoryNPCRepository()
        generator = FakeProfileGenerator()
        manager = NPCManager(
            repository=repository,
            profile_generator=generator,
        )

        record = manager.acquire(requirement())

        self.assertEqual(NPCStatus.READY, record.profile.status)
        self.assertIn("public_request", record.profile.knows)
        self.assertIn("main_ending", record.profile.must_not_know)
        self.assertEqual(1, generator.calls)
        self.assertEqual(1, manager.metrics.generated_count)

    def test_reuses_exact_candidate_without_model_call(self) -> None:
        existing = NPCRecord(profile=profile(), story_ids=("old_story",))
        repository = InMemoryNPCRepository((existing,))
        generator = FakeProfileGenerator()
        manager = NPCManager(
            repository=repository,
            profile_generator=generator,
        )

        record = manager.acquire(requirement())

        self.assertEqual("npc_1", record.profile.npc_id)
        self.assertEqual(0, generator.calls)
        self.assertEqual(1, manager.metrics.reuse_count)
        self.assertIn("story_1", record.story_ids)

    def test_does_not_reuse_npc_with_forbidden_knowledge(self) -> None:
        existing = NPCRecord(profile=profile(knows=("main_ending",)))
        repository = InMemoryNPCRepository((existing,))
        generator = FakeProfileGenerator()
        manager = NPCManager(
            repository=repository,
            profile_generator=generator,
        )

        record = manager.acquire(requirement())

        self.assertNotEqual("npc_1", record.profile.npc_id)
        self.assertEqual(1, generator.calls)

    def test_activation_and_release_follow_lifecycle(self) -> None:
        repository = InMemoryNPCRepository()
        generator = FakeProfileGenerator()
        runtime = FakeRuntime()
        manager = NPCManager(
            repository=repository,
            profile_generator=generator,
            runtime=runtime,
        )
        record = manager.acquire(requirement())

        active = manager.activate(
            record.profile.npc_id,
            story_id="story_1",
            side_arc_id="side_1",
            scene_id="scene_1",
            reason="Director approved entry",
        )
        manager.request_proposal(
            active.profile.npc_id,
            DirectorTask(
                task_id="task_1",
                source=TaskSource.DIRECTOR_TASK,
                source_reference="beat_1",
                objective="Deliver the request",
            ),
        )
        inactive = manager.deactivate(
            active.profile.npc_id,
            reason="Scene ended",
        )
        completed = manager.complete(
            inactive.profile.npc_id,
            reason="Side arc completed",
        )

        self.assertEqual(NPCStatus.ACTIVE, active.profile.status)
        self.assertEqual(NPCStatus.INACTIVE, inactive.profile.status)
        self.assertEqual(NPCStatus.COMPLETED, completed.profile.status)
        self.assertEqual([record.profile.npc_id], runtime.turns)
        self.assertEqual(1, manager.metrics.runtime_turns)

    def test_archives_all_npcs_from_completed_main_story(self) -> None:
        repository = InMemoryNPCRepository()
        manager = NPCManager(
            repository=repository,
            profile_generator=FakeProfileGenerator(),
        )
        record = manager.acquire(requirement())

        archived = manager.archive_story(
            "story_1",
            reason="Main arc ended",
        )

        self.assertEqual(1, len(archived))
        self.assertEqual(NPCStatus.ARCHIVED, archived[0].profile.status)

    def test_archived_npc_can_be_reused_in_a_later_story(self) -> None:
        archived = NPCRecord(
            profile=profile(status=NPCStatus.ARCHIVED),
            story_ids=("old_story",),
        )
        repository = InMemoryNPCRepository((archived,))
        generator = FakeProfileGenerator()
        manager = NPCManager(
            repository=repository,
            profile_generator=generator,
        )
        later_requirement = replace(
            requirement(),
            story_id="story_2",
            requirement_id="req_2",
        )

        reused = manager.acquire(later_requirement)
        active = manager.activate(
            reused.profile.npc_id,
            story_id="story_2",
            side_arc_id="side_1",
            scene_id="scene_2",
            reason="Reused in a later story",
        )

        self.assertEqual(0, generator.calls)
        self.assertEqual(NPCStatus.READY, reused.profile.status)
        self.assertEqual(NPCStatus.ACTIVE, active.profile.status)
        self.assertEqual(("old_story", "story_2"), active.story_ids)

    def test_only_main_arc_end_can_archive(self) -> None:
        record = NPCRecord(profile=profile())
        with self.assertRaises(InvalidLifecycleTransition):
            transition(
                record,
                target=NPCStatus.ARCHIVED,
                trigger=TransitionTrigger.SCENE_ENDED,
                reason="Wrong trigger",
            )

    def test_records_only_accepted_safe_memory(self) -> None:
        repository = InMemoryNPCRepository()
        manager = NPCManager(
            repository=repository,
            profile_generator=FakeProfileGenerator(),
        )
        record = manager.acquire(requirement())

        updated = manager.record_accepted_event(
            record.profile.npc_id,
            AcceptedNPCEvent(
                event_id="event_1",
                summary="The request was delivered.",
                learned_facts=("safe_fact", "main_ending"),
                relation_to_user_update="The user listened.",
            ),
        )

        self.assertIn("safe_fact", updated.memory.learned_facts)
        self.assertNotIn("main_ending", updated.memory.learned_facts)
        self.assertEqual(("The user listened.",), updated.memory.relation_to_user)


class AsyncNPCManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_runtime_supports_gateway_turns(self) -> None:
        repository = InMemoryNPCRepository()
        manager = NPCManager(
            repository=repository,
            profile_generator=FakeProfileGenerator(),
            async_runtime=FakeAsyncRuntime(),
        )
        record = manager.acquire(requirement())
        active = manager.activate(
            record.profile.npc_id,
            story_id="story_1",
            side_arc_id="side_1",
            scene_id="scene_1",
            reason="Director approved entry",
        )
        task = DirectorTask(
            task_id="task_1",
            source=TaskSource.DIRECTOR_TASK,
            source_reference="beat_1",
            objective="Deliver the request",
        )

        proposal, validation = await manager.request_proposal_async(
            active.profile.npc_id,
            task,
        )

        self.assertEqual(active.profile.npc_id, proposal.npc_id)
        self.assertTrue(validation.is_valid)
        self.assertEqual(1, manager.metrics.runtime_turns)


class PermissionTests(unittest.TestCase):
    def test_turn_context_filters_forbidden_information(self) -> None:
        npc_profile = replace(
            profile(status=NPCStatus.ACTIVE),
            knows=("safe_fact",),
            must_not_know=("secret",),
        )
        record = NPCRecord(profile=npc_profile)
        task = DirectorTask(
            task_id="task_1",
            source=TaskSource.DIRECTOR_TASK,
            source_reference="beat_1",
            objective="Deliver the request",
            visible_events=("public_event", "secret"),
            known_facts=("task_fact", "secret"),
        )

        context = build_turn_context(record, task)

        self.assertEqual(("public_event",), context.visible_events)
        self.assertEqual(("safe_fact", "task_fact"), context.known_facts)

    def test_proactive_proposal_requires_allowed_actions(self) -> None:
        record = NPCRecord(profile=profile(status=NPCStatus.ACTIVE))
        task = DirectorTask(
            task_id="task_1",
            source=TaskSource.DIRECTOR_TASK,
            source_reference="beat_1",
            objective="Wait",
        )
        proposal = NPCProposal(
            npc_id="npc_1",
            intent="start_event",
            action="Approach the user",
            proactive=True,
        )

        result = validate_proposal(record, task, proposal)

        self.assertFalse(result.is_valid)
        self.assertIn("proactive_action_not_authorized", result.issues)


class NPCPromptTests(unittest.TestCase):
    def test_dynamic_input_contains_only_current_npc_context(self) -> None:
        npc_profile = replace(
            profile(status=NPCStatus.ACTIVE),
            knows=("safe_fact",),
            must_not_know=("hidden_ending",),
        )
        record = NPCRecord(profile=npc_profile)
        task = DirectorTask(
            task_id="task_1",
            source=TaskSource.DIRECTOR_TASK,
            source_reference="beat_1",
            objective="Deliver the request",
            visible_events=("visible_event",),
            known_facts=("safe_task_fact", "hidden_ending"),
            allowed_actions=("approach_yangjian",),
        )
        turn_context = build_turn_context(record, task)

        payload = build_npc_turn_input(turn_context)
        raw = build_npc_turn_input_json(turn_context)

        self.assertEqual("npc_1", payload["npc_profile"]["npc_id"])
        self.assertEqual(
            ["safe_fact", "safe_task_fact"],
            payload["current_scene"]["known_facts"],
        )
        self.assertNotIn("must_not_know", payload["npc_profile"])
        self.assertNotIn("hidden_ending", raw)

    def test_fixed_prompt_and_schema_preserve_director_authority(self) -> None:
        self.assertIn("行动结果由导演裁决", NPC_BASE_SYSTEM_PROMPT)
        self.assertIn("proposed_effects", NPC_PROPOSAL_SCHEMA["required"])


class RegistryAndCodecTests(unittest.TestCase):
    def test_candidates_are_sorted_by_deterministic_score(self) -> None:
        exact = NPCRecord(profile=profile(npc_id="exact"))
        partial_profile = replace(
            profile(npc_id="partial"),
            short_background="Different background",
            relation_to_user="Known contact",
        )
        partial = NPCRecord(profile=partial_profile)

        candidates = find_reuse_candidates(requirement(), (partial, exact))

        self.assertEqual("exact", candidates[0].record.profile.npc_id)
        self.assertGreater(candidates[0].score, candidates[1].score)

    def test_record_json_round_trip(self) -> None:
        original = NPCRecord(
            profile=profile(status=NPCStatus.INACTIVE),
            story_ids=("story_1",),
            last_transition_reason="Scene ended",
        )

        restored = npc_record_from_json(npc_record_to_json(original))

        self.assertEqual(original, restored)


if __name__ == "__main__":
    unittest.main()
