from __future__ import annotations

import unittest

from room import room


class ResolveGateTests(unittest.TestCase):
    def test_auto_accept_actor_results_publishes_dialogue(self) -> None:
        actor_result = {
            "result_id": "proposal_1",
            "task_id": "task_1",
            "agent_id": "yangjian",
            "kind": "proposal",
            "proposal": {
                "proposal_id": "proposal_1",
                "task_id": "task_1",
                "agent_id": "yangjian",
                "intent": "respond",
                "dialogue": {"text": "我在。", "intent": "reply"},
                "action": None,
            },
            "abstention": None,
        }
        outputs, events = room._auto_accept_actor_results(
            [actor_result],
            [],
        )
        self.assertEqual("杨戬", outputs[0]["role"])
        self.assertEqual("我在。", outputs[0]["text"])
        self.assertTrue(events)

    def test_confirmed_events_from_user_feedback(self) -> None:
        events = room._confirmed_events_from_user_feedback({
            "outcome_summary": "盒中是玉符",
            "revealed_fact_ids": ["fact_jade"],
            "presentation": {
                "required": True,
                "purpose": "visible_action",
                "timing": "before_dialogue",
            },
        })
        self.assertEqual(1, len(events))
        self.assertEqual("user_action", events[0]["event_type"])

    def test_select_narration_spec_prefers_inline_feedback(self) -> None:
        spec = room._select_narration_spec(
            {
                "inline_effects": {
                    "user_feedback": {
                        "outcome_summary": "盒中是玉符",
                        "revealed_fact_ids": ["fact_jade"],
                        "presentation": {
                            "required": True,
                            "purpose": "visible_action",
                            "timing": "before_dialogue",
                        },
                    }
                },
                "narration_request": {
                    "purpose": "external_event",
                    "timing": "after_dialogue",
                    "visible_fact_ids": [],
                    "max_characters": 80,
                },
            },
            {},
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual("before_dialogue", spec["timing"])
        self.assertEqual(["fact_jade"], spec["visible_fact_ids"])


    def test_synthetic_narration_events_from_scene_facts(self) -> None:
        events = room._synthetic_confirmed_events_for_narration({
            "brief": "确认异常现象出现",
            "scene_facts": ["古盒散发微光"],
        })
        self.assertEqual(2, len(events))


if __name__ == "__main__":
    unittest.main()
