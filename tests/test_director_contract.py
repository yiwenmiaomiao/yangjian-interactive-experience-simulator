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
            "available_side_arcs": [],
        }
        canonical = director._enrich_canonical_directive({
            "mode": "DIRECT",
            "chapter": "story_1",
            "beat": "m1",
            "observed_user_intent": {"intent": "continue", "confidence": 0.5},
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
            "selected_side_arc": None,
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
