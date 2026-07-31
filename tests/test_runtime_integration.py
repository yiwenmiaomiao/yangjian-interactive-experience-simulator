import json
import os
import tempfile
import unittest
from unittest.mock import patch

from room import director, narrator, npc_manager_runtime, room, yangjian
import runtime_context
import state_manager
import story_facts
import story_state
from yangjian_story_generator import preference_store
from room.npc_manager import (
    InMemoryNPCRepository,
    JsonNPCRepository,
    NPCManager,
    NPCMemory,
    NPCProfile,
    NPCProposal,
    NPCRecord,
    NPCStatus,
)


class DirectorRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.beat_info = {
            "story_id": "story_1",
            "current_beat_id": "m1",
            "allowed_information": ["visible_fact"],
            "forbidden_reveals": ["hidden_fact"],
            "available_transitions": [
                {"transition_id": "to_m2", "target_id": "m2"}
            ],
            "available_side_arcs": [],
        }

    def test_direct_guard_rejects_information_not_whitelisted_by_room(self) -> None:
        directive = {
            "mode": "DIRECT",
            "chapter": "story_1",
            "beat": "m1",
            "observed_user_intent": {"intent": "continue", "confidence": 0.5},
            "tasks": [{
                "task_id": "task_1",
                "target": "yangjian",
                "source_reference": "m1",
                "objective": "回应当前局面",
                "information_ids": ["invented_fact"],
                "success_condition": "产生符合角色的行动",
            }],
            "desired_progress": "maintain",
            "selected_side_arc": None,
            "narration": {
                "required": False,
                "purpose": "none",
                "timing": "none",
                "visible_facts": [],
                "max_characters": 0,
            },
            "npc_commands": [],
            "fallback_world_event": None,
        }
        report = director.validate_canonical_directive(
            directive, self.beat_info
        )
        self.assertFalse(report.is_valid)
        self.assertIn(
            "INFORMATION_NOT_ALLOWED",
            {issue.code for issue in report.issues},
        )

    def test_resolution_guard_rejects_locked_next_beat(self) -> None:
        proposals = [{
            "proposal_id": "proposal_1",
            "role": "杨戬",
            "text": "知道了。",
            "kind": "dialogue",
        }]
        resolution = {
            "mode": "RESOLVE",
            "chapter": "story_1",
            "beat": "m1",
            "decisions": [{
                "proposal_id": "proposal_1",
                "result": "accept",
                "outcome_summary": "杨戬作出回应",
            }],
            "state_changes": [],
            "next_beat": "locked_beat",
        }
        report = director.validate_canonical_resolution(
            resolution, proposals, self.beat_info
        )
        self.assertFalse(report.is_valid)
        self.assertIn("NEXT_BEAT_LOCKED", {issue.code for issue in report.issues})


class RoomAdjudicationTests(unittest.TestCase):
    def test_only_adjudicated_outputs_are_published(self) -> None:
        proposals = [
            {
                "proposal_id": "p1",
                "role": "杨戬",
                "text": "原台词",
                "kind": "dialogue",
            },
            {
                "proposal_id": "p2",
                "role": "杨戬的动作",
                "text": "原动作",
                "kind": "action",
            },
            {
                "proposal_id": "p3",
                "role": "NPC",
                "text": "应被拒绝",
                "kind": "dialogue",
            },
        ]
        resolution = {
            "decisions": [
                {
                    "proposal_id": "p1",
                    "result": "accept",
                    "outcome_summary": "杨戬说出原台词",
                },
                {
                    "proposal_id": "p2",
                    "result": "modify",
                    "outcome_summary": "动作被调整",
                },
                {
                    "proposal_id": "p3",
                    "result": "reject",
                    "outcome_summary": "NPC没有开口",
                },
            ]
        }

        outputs, outcomes = room._apply_resolution(proposals, resolution, [])

        self.assertEqual(
            [
                {"role": "杨戬", "text": "原台词"},
                {"role": "杨戬的动作", "text": "动作被调整"},
            ],
            outputs,
        )
        self.assertEqual(2, len(outcomes))

    def test_forbidden_reveal_is_removed_before_delivery(self) -> None:
        proposals = [{
            "proposal_id": "p1",
            "role": "杨戬",
            "text": "古盒与瑶姬有关",
            "kind": "dialogue",
        }]
        resolution = {
            "decisions": [{
                "proposal_id": "p1",
                "result": "accept",
                "outcome_summary": "杨戬回答",
            }]
        }
        outputs, _ = room._apply_resolution(
            proposals, resolution, ["不能透露古盒与瑶姬有关"]
        )
        self.assertEqual([], outputs)


class AgentRuntimeTests(unittest.TestCase):
    def test_yangjian_accepts_flat_goal_and_corner_bracket_action(self) -> None:
        with (
            patch.object(yangjian, "_load_soul", return_value="SOUL"),
            patch.object(
                yangjian.llm,
                "call",
                return_value="「抬手示意」\n先等等。",
            ) as call,
        ):
            result = yangjian.act(
                {
                    "scene": "m1",
                    "outcome": "回应",
                    "goals": {"杨戬": "谨慎回应用户"},
                },
                "可见信息",
            )
        self.assertEqual(["抬手示意"], result["actions"])
        self.assertEqual(["先等等。"], result["dialogues"])
        self.assertIn("谨慎回应用户", call.call_args.kwargs["messages"][0]["content"])

    def test_npc_runtime_uses_current_manager_contract(self) -> None:
        profile = NPCProfile(
            npc_id="npc_1",
            status=NPCStatus.ACTIVE,
            name="路人",
            public_role="商贩",
            short_background="在附近摆摊",
            current_goal="完成交易",
            relation_to_yangjian="陌生",
            relation_to_user="陌生",
            expression_style="直接",
        )
        record = NPCRecord(profile=profile, memory=NPCMemory())

        class ProposalRuntime:
            def run_turn(self, context):
                return NPCProposal(
                    npc_id=context.npc_id,
                    intent="respond",
                    utterance="客官要看看吗？",
                )

        manager = NPCManager(
            repository=InMemoryNPCRepository((record,)),
            profile_generator=None,
            runtime=ProposalRuntime(),
        )
        with patch.object(
            npc_manager_runtime, "_get_manager", return_value=manager
        ):
            result = npc_manager_runtime.act_for_task(
                "npc_1",
                {
                    "objective": "回应用户",
                    "source_reference": "m1",
                    "allowed_actions": ["speak"],
                },
                ["用户走近摊位"],
            )
        self.assertEqual(["客官要看看吗？"], result["dialogues"])
        self.assertEqual(1, manager.metrics.runtime_turns)

    def test_json_npc_repository_survives_reconstruction(self) -> None:
        profile = NPCProfile(
            npc_id="npc_saved",
            status=NPCStatus.READY,
            name="守门人",
            public_role="守卫",
            short_background="守在门前",
            current_goal="守门",
            relation_to_yangjian="敬重",
            relation_to_user="陌生",
            expression_style="简短",
        )
        record = NPCRecord(profile=profile)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "npcs.json")
            JsonNPCRepository(path).save(record)
            restored = JsonNPCRepository(path).get("npc_saved")
        self.assertIsNotNone(restored)
        self.assertEqual("守门人", restored.profile.name)

    def test_narrator_receives_confirmed_shared_facts(self) -> None:
        with patch.object(narrator.llm, "call", return_value="石台上的古盒没有移动。") as call:
            result = narrator.speak(
                {
                    "scene": "庭院",
                    "outcome": "杨戬停在石台旁",
                    "order": ["旁白"],
                    "facts_summary": "古盒 → 石台上",
                },
                {"event_log": []},
                max_chars=100,
            )
        self.assertEqual("石台上的古盒没有移动。", result)
        self.assertIn(
            "古盒 → 石台上",
            call.call_args.kwargs["messages"][0]["content"],
        )


class RuntimeIsolationTests(unittest.TestCase):
    def test_user_and_thread_receive_separate_persistent_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            world_path = os.path.join(directory, "world_state.json")
            story_path = os.path.join(directory, "story_state.json")
            facts_path = os.path.join(directory, "world_facts.json")
            with open(world_path, "w", encoding="utf-8") as handle:
                json.dump({"tick": 0, "mood": "base", "stories": {}}, handle)

            with (
                patch.object(state_manager, "STATE_PATH", world_path),
                patch.object(story_state, "STORY_STATE_PATH", story_path),
                patch.object(story_facts, "FACTS_PATH", facts_path),
            ):
                token = runtime_context.set_identity("user_a", "thread_1")
                try:
                    world = state_manager.load()
                    world["mood"] = "user_a"
                    state_manager.save(world)
                    story_state.save_state({
                        **story_state.default_state(),
                        "status": "active",
                        "current_beat_id": "m2",
                    })
                    facts = story_facts.default_facts()
                    facts["current_scene"] = "scene_a"
                    story_facts.save_facts(facts)
                finally:
                    runtime_context.reset_identity(token)

                token = runtime_context.set_identity("user_b", "thread_1")
                try:
                    self.assertEqual("base", state_manager.load()["mood"])
                    self.assertEqual(
                        "inactive", story_state.load_state()["status"]
                    )
                    self.assertEqual(
                        "", story_facts.load_facts()["current_scene"]
                    )
                finally:
                    runtime_context.reset_identity(token)

    def test_explicit_user_feedback_is_recorded_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "preferences.json")
            with patch.object(preference_store, "DEFAULT_STORE_PATH", path):
                token = runtime_context.set_identity("user_a", "thread_1")
                try:
                    room._capture_explicit_preferences(
                        "我喜欢搞笑一点，旁白简洁一些", "user_a"
                    )
                    scoped = runtime_context.scoped_path(path)
                finally:
                    runtime_context.reset_identity(token)
            signals = preference_store.PreferenceStore(
                scoped, user_id="user_a"
            ).list_signals()
            with open(scoped, encoding="utf-8") as handle:
                raw_signals = json.load(handle)
        self.assertEqual(2, len(signals))
        self.assertTrue(
            all(item["user_id"] == "user_a" for item in raw_signals)
        )


if __name__ == "__main__":
    unittest.main()
