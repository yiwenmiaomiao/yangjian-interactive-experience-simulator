from __future__ import annotations

import unittest

from room import director


class DirectorContractTests(unittest.TestCase):
    def test_enrich_adds_story_profile_commands_and_never_hold(self) -> None:
        beat = {
            "story_id": "story_1",
            "current_beat_id": "m1",
            "allowed_information": [],
            "active_npcs": [],
            "npc_profiles": [
                {"profile_id": "profile_guard", "requirement_id": "req_guard"}
            ],
            "available_transitions": [],
        }
        canonical = director._enrich_canonical_directive({
            "mode": "DIRECT",
            "chapter": "story_1",
            "beat": "m1",
            "observed_user_intent": {"intent": "continue", "confidence": 0.5},
            "user_turn": {
                "kind": "dialogue",
                "target": None,
                "disclosure": {"required": False, "mode": "none"},
            },
            "resolve_gate": {
                "required": True,
                "reason": "default_full_path",
                "act_required": True,
            },
            "inline_effects": {
                "state_operations": [],
                "user_feedback": None,
            },
            "tasks": [{
                "task_id": "task_yangjian",
                "target": "yangjian",
                "objective": "回应当前局面",
                "source_reference": "m1",
                "information_ids": [],
                "success_condition": "产生符合角色的行动",
            }],
            "npc_commands": [],
            "desired_progress": "maintain",
            "narration": {
                "required": False,
                "purpose": "none",
                "timing": "none",
                "visible_facts": [],
                "max_characters": 0,
            },
            "fallback_world_event": None,
        }, beat)
        operations = {
            item["operation"] for item in canonical["npc_commands"]
        }
        self.assertEqual({"ensure_registered", "activate"}, operations)
        self.assertNotIn("hold", canonical)

    def test_sanitize_narration_keeps_director_task(self) -> None:
        beat = {
            "story_id": "story_1",
            "current_beat_id": "m1",
            "allowed_information": [],
            "active_npcs": [],
            "npc_profiles": [],
            "available_transitions": [],
            "forbidden_information": [],
        }
        payload = {
            "mode": "DIRECT",
            "chapter": "m1",
            "beat": "m1",
            "observed_user_intent": {"intent": "continue", "confidence": 0.2},
            "tasks": [{
                "task_id": "task_yangjian_m1",
                "target": "yangjian",
                "source_reference": "m1",
                "objective": "面对古盒做出反应",
                "information_ids": [],
                "success_condition": "符合人设",
            }],
            "npc_commands": [],
            "desired_progress": "maintain",
            "narration": {
                "required": True,
                "purpose": "确认异常现象出现在杨戬和用户面前",
                "timing": "immediate",
                "visible_facts": [
                    "灌江口出现散发微光的古盒",
                    "空气中弥漫着不寻常的法力波动",
                ],
                "max_characters": 200,
            },
            "fallback_world_event": None,
        }
        report = director.validate_canonical_directive(payload, beat)
        self.assertTrue(report.is_valid, [issue.code for issue in report.issues])
        canonical = director._sanitize_canonical_directive(
            director._enrich_canonical_directive(
                director._coerce_canonical_directive(payload, beat),
                beat,
            ),
            beat,
        )
        runtime = director._canonical_directive_to_runtime(canonical, beat)
        self.assertEqual(
            "面对古盒做出反应",
            runtime["actor_tasks"][0]["objective"],
        )
        self.assertEqual(
            "before_dialogue",
            runtime["narration_request"]["timing"],
        )
        self.assertTrue(runtime["narration_request"]["scene_facts"])

    def test_resolution_always_has_continuation_for_abstention(self) -> None:
        abstention = {
            "result_id": "abstain_1",
            "task_id": "task_yangjian",
            "agent_id": "yangjian",
            "kind": "abstain",
            "abstention": {
                "reason_code": "NEEDS_CONTEXT",
                "reason": "No grounded action is available",
            },
            "proposal": None,
        }
        normalized = director._normalize_resolution(
            {"decisions": [], "state_changes": [], "next_beat": None},
            [abstention],
            {"story_id": "story_1", "current_beat_id": "m1"},
        )
        self.assertEqual(
            "accept_abstention", normalized["decisions"][0]["result"]
        )
        self.assertEqual(
            "continue_current", normalized["continuation"]["kind"]
        )


if __name__ == "__main__":
    unittest.main()
