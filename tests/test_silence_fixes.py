from __future__ import annotations

import unittest
from unittest.mock import patch

from room import director
from room import imessage_poller as poller


class ResolveSilenceFixTests(unittest.TestCase):
    def test_resolve_context_allows_item_and_reveal_keys(self) -> None:
        bi = {
            "story_id": "story_1",
            "current_beat_id": "m1",
            "allowed_information": ["fact_box"],
            "story_items": ["古盒"],
            "story_characters": ["杨戬"],
            "available_transitions": [],
        }
        ctx = director._build_resolve_context(
            bi,
            [{"result_id": "r1"}],
        )
        self.assertIn("item_古盒", ctx.allowed_state_change_keys)
        self.assertIn("reveal_fact_box", ctx.allowed_state_change_keys)
        self.assertIn("character_杨戬", ctx.allowed_state_change_keys)

    def test_fallback_resolution_accepts_proposals(self) -> None:
        bi = {"story_id": "story_1", "current_beat_id": "m1"}
        proposals = [{
            "result_id": "r1",
            "kind": "proposal",
            "proposal": {
                "dialogue": {"text": "你好", "intent": "greet"},
                "action": None,
            },
        }]
        resolution = director._fallback_resolution(
            proposals, bi, reason="guard failed"
        )
        self.assertEqual("accept", resolution["decisions"][0]["result"])
        self.assertEqual(
            {"text": "你好", "intent": "greet"},
            resolution["decisions"][0]["final_dialogue"],
        )

    def test_normalize_strips_illegal_state_keys(self) -> None:
        bi = {
            "story_id": "story_1",
            "current_beat_id": "m1",
            "allowed_information": [],
            "story_items": [],
            "story_characters": [],
        }
        resolution = director._normalize_resolution(
            {
                "decisions": [{
                    "proposal_id": "r1",
                    "result": "accept",
                    "outcome_summary": "ok",
                }],
                "state_changes": [
                    {"key": "trust", "value": 1, "reason": "ok"},
                    {"key": "secret_hack", "value": 1, "reason": "bad"},
                ],
                "continuation": {
                    "kind": "continue_current",
                    "reason": "ok",
                    "target_id": None,
                    "world_event": None,
                },
            },
            [{
                "result_id": "r1",
                "kind": "proposal",
                "proposal": {
                    "dialogue": {"text": "hi", "intent": "x"},
                },
            }],
            bi,
        )
        keys = [c["key"] for c in resolution["state_changes"]]
        self.assertEqual(["trust"], keys)


class ImessagePollerTests(unittest.TestCase):
    def test_process_message_advances_when_sent_zero(self) -> None:
        fake_bridge = type(
            "Bridge",
            (),
            {
                "handle_and_deliver": staticmethod(
                    lambda *a, **k: {
                        "ok": True,
                        "output": [{"role": "杨戬", "text": "hi"}],
                        "delivery": {"sent": 0, "skipped": 1},
                    }
                )
            },
        )
        with patch("importlib.import_module", return_value=fake_bridge):
            self.assertTrue(poller.process_message("你好"))


if __name__ == "__main__":
    unittest.main()
