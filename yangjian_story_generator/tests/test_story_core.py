from __future__ import annotations

import unittest
from dataclasses import replace

from yangjian_story_generator import (
    AsyncStoryGenerator,
    BranchTransition,
    CanonicalEvent,
    CharacterContext,
    Ending,
    MainArc,
    NarrativeFunction,
    NPCProfileSpec,
    NPCRequirement,
    PreferenceSnapshot,
    SideArc,
    StoryBeat,
    StoryBrief,
    StoryGenerator,
    StoryPlan,
    StoryPlanValidator,
    story_plan_from_json,
    story_plan_public_summary,
    story_plan_to_dict,
    story_plan_to_json,
)


def valid_plan() -> StoryPlan:
    main = MainArc(
        goal="Develop the relationship between the user and Yang Jian.",
        beats=(
            StoryBeat(
                beat_id="m1",
                plot="Establish a shared problem.",
                participants=("user", "yangjian"),
                transitions=(
                    BranchTransition(
                        transition_id="m1_to_end",
                        target_id="main_end",
                        preserved_consequences=("shared_problem_acknowledged",),
                    ),
                    BranchTransition(
                        transition_id="m1_to_s1",
                        target_id="s1",
                        goal="An external event pulls the user into the side arc.",
                    ),
                ),
            ),
        ),
        endings=(
            Ending(
                ending_id="main_end",
                summary="The relationship reaches a stable outcome.",
            ),
        ),
    )
    npc = NPCRequirement(
        requirement_id="npc_req_1",
        story_id="story_1",
        arc_id="side_1",
        narrative_function=NarrativeFunction.CATALYST,
        purpose="Introduce a side event that tests cooperation.",
        npc_background="Fits the current world.",
        relation_to_yangjian="Acquaintance",
        relation_to_user="Stranger",
        current_goal="Request assistance.",
        must_not_know=("main_ending",),
    )
    side = SideArc(
        arc_id="side_1",
        purpose="Test cooperation through a temporary event.",
        impact_on_main_arc=("Changes mutual trust.",),
        beats=(
            StoryBeat(
                beat_id="s1",
                plot="Respond to the NPC request.",
                participants=("user", "yangjian", "npc_1"),
                npc_requirement_ids=("npc_req_1",),
                transitions=(
                    BranchTransition(
                        transition_id="s1_to_main_end",
                        target_id="main_end",
                        goal="The side event concludes and the story returns to the main arc.",
                    ),
                ),
            ),
        ),
        npc_requirements=(npc,),
    )
    return StoryPlan(
        story_id="story_1",
        created_at="2026-07-30T00:00:00Z",
        premise="A private test premise.",
        theme="Trust",
        main_arc=main,
        side_arcs=(side,),
        npc_profiles=(
            NPCProfileSpec(
                profile_id="npc_1",
                requirement_id="npc_req_1",
                narrative_function=NarrativeFunction.CATALYST,
                name="Messenger",
                public_role="Temporary messenger",
                personality=("direct",),
                background="Carries a request into the current scene.",
                expression_style="brief",
                goals=("Request assistance.",),
                relation_to_yangjian="Acquaintance",
                relation_to_user="Stranger",
                must_not_know=("main_ending",),
                story_bindings=("side_1", "s1"),
            ),
        ),
    )


class StoryValidationTests(unittest.TestCase):
    def test_valid_plan_passes(self) -> None:
        report = StoryPlanValidator().validate(valid_plan())
        self.assertTrue(report.is_valid, report.issues)

    def test_main_arc_rejects_more_than_two_endings(self) -> None:
        plan = valid_plan()
        endings = (
            *plan.main_arc.endings,
            Ending(ending_id="main_end_2", summary="Alternative outcome."),
            Ending(ending_id="main_end_3", summary="Third outcome."),
        )
        invalid = replace(plan, main_arc=replace(plan.main_arc, endings=endings))

        report = StoryPlanValidator().validate(invalid)

        self.assertFalse(report.is_valid)
        self.assertTrue(report.by_code("MAIN_ENDING_COUNT"))

    def test_unreachable_beat_is_reported(self) -> None:
        plan = valid_plan()
        orphan = StoryBeat(
            beat_id="orphan",
            plot="This beat is disconnected.",
            participants=("user", "yangjian"),
            transitions=(
                BranchTransition(
                    transition_id="orphan_to_end",
                    target_id="main_end",
                ),
            ),
        )
        invalid = replace(
            plan,
            main_arc=replace(
                plan.main_arc,
                beats=(*plan.main_arc.beats, orphan),
            ),
        )

        report = StoryPlanValidator().validate(invalid)

        self.assertTrue(report.by_code("UNREACHABLE_NODE"))

    def test_unknown_npc_requirement_is_reported(self) -> None:
        plan = valid_plan()
        side = plan.side_arcs[0]
        beat = replace(side.beats[0], npc_requirement_ids=("missing",))
        invalid = replace(
            plan,
            side_arcs=(replace(side, beats=(beat,)),),
        )

        report = StoryPlanValidator().validate(invalid)

        self.assertTrue(report.by_code("UNKNOWN_NPC_REQUIREMENT"))

    def test_each_npc_requirement_needs_a_complete_profile(self) -> None:
        invalid = replace(valid_plan(), npc_profiles=())
        report = StoryPlanValidator().validate(invalid)
        self.assertTrue(report.by_code("NPC_PROFILE_MISSING"))

    def test_npc_requirement_must_match_story_and_side_arc(self) -> None:
        plan = valid_plan()
        side = plan.side_arcs[0]
        npc = replace(
            side.npc_requirements[0],
            story_id="other_story",
            arc_id="other_arc",
        )
        invalid = replace(
            plan,
            side_arcs=(replace(side, npc_requirements=(npc,)),),
        )

        report = StoryPlanValidator().validate(invalid)

        self.assertTrue(report.by_code("NPC_REQUIREMENT_STORY_MISMATCH"))
        self.assertTrue(report.by_code("NPC_REQUIREMENT_ARC_MISMATCH"))


class StoryCodecTests(unittest.TestCase):
    def test_private_json_round_trip(self) -> None:
        plan = valid_plan()
        restored = story_plan_from_json(story_plan_to_json(plan))
        self.assertEqual(plan, restored)

    def test_public_summary_excludes_spoilers(self) -> None:
        plan = valid_plan()
        summary = story_plan_public_summary(plan)
        private = story_plan_to_dict(plan)

        self.assertNotIn("premise", summary)
        self.assertNotIn("main_arc", summary)
        self.assertNotIn("forbidden_reveals", summary)
        self.assertIn("main_arc", private)


class FakeModelClient:
    def __init__(self, plan: StoryPlan) -> None:
        self.plan = plan
        self.last_payload = None

    def generate_story_plan(self, payload):
        self.last_payload = payload
        return story_plan_to_dict(self.plan)


class StoryGeneratorTests(unittest.TestCase):
    def test_generator_is_model_adapter_agnostic(self) -> None:
        model = FakeModelClient(valid_plan())
        generator = StoryGenerator(model)
        character = CharacterContext(
            character_id="yangjian",
            source_version="canonical-v1",
            canonical_history=(
                CanonicalEvent(
                    event_id="event_1",
                    summary="A canonical event.",
                    source_reference="source:1",
                ),
            ),
        )
        preferences = PreferenceSnapshot(
            user_id="single-user",
            profile_version=1,
            created_at="2026-07-30T00:00:00Z",
        )

        generated = generator.generate(
            character=character,
            preferences=preferences,
            brief=StoryBrief(
                story_id="story_1",
                created_at="2026-07-30T00:00:00Z",
            ),
        )

        self.assertEqual("story_1", generated.story_id)
        self.assertEqual("generate_private_branching_story_plan", model.last_payload["task"])


class FakeAsyncModelClient:
    def __init__(self, plan: StoryPlan) -> None:
        self.plan = plan

    async def generate_story_plan(self, payload):
        return story_plan_to_dict(self.plan)


class AsyncStoryGeneratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_generator_supports_gateway_adapters(self) -> None:
        generator = AsyncStoryGenerator(FakeAsyncModelClient(valid_plan()))
        character = CharacterContext(
            character_id="yangjian",
            source_version="canonical-v1",
            canonical_history=(
                CanonicalEvent(
                    event_id="event_1",
                    summary="A canonical event.",
                    source_reference="source:1",
                ),
            ),
        )
        preferences = PreferenceSnapshot(
            user_id="single-user",
            profile_version=1,
            created_at="2026-07-30T00:00:00Z",
        )

        generated = await generator.generate(
            character=character,
            preferences=preferences,
            brief=StoryBrief(
                story_id="story_1",
                created_at="2026-07-30T00:00:00Z",
            ),
        )

        self.assertEqual("story_1", generated.story_id)


if __name__ == "__main__":
    unittest.main()
