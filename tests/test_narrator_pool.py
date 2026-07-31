from __future__ import annotations

import unittest
from unittest.mock import patch

from room import contracts, narrator


class NarratorPoolTests(unittest.TestCase):
    def test_narrator_endpoint_uses_narrate_phase(self) -> None:
        request = contracts.new_message(
            turn_id="turn_1",
            story_id="story_1",
            beat_id="m1",
            phase=contracts.Phase.NARRATE,
            sender=contracts.AgentRef(
                agent_id="room", kind=contracts.AgentKind.ROOM
            ),
            recipient=contracts.AgentRef(
                agent_id="narrator", kind=contracts.AgentKind.NARRATOR
            ),
            message_type="narrator.input",
            payload=contracts.NarratorInput(
                narration_request=contracts.NarrationRequest(
                    purpose="visible_action"
                ),
                scene={"id": "gate"},
                confirmed_events=({
                    "event_id": "event_1",
                    "summary": "门已打开",
                },),
                visible_facts=(),
            ),
        )
        with patch.object(
            narrator.llm, "call", return_value="夜色渐深。"
        ):
            response = narrator.handle_message(request)
        self.assertEqual(contracts.Phase.NARRATE, response.phase)
        self.assertEqual(request.message_id, response.correlation_id)
        self.assertEqual("narrator.draft", response.message_type)

    def test_narrator_only_uses_confirmed_events(self) -> None:
        with patch.object(
            narrator.llm, "call", return_value="门在你面前打开。"
        ) as call:
            result = narrator.draft(
                contracts.NarratorInput(
                    narration_request=contracts.NarrationRequest(
                        purpose="visible_action"
                    ),
                    scene={"id": "gate"},
                    confirmed_events=({
                        "event_id": "event_1",
                        "summary": "门已打开",
                    },),
                    visible_facts=(),
                )
            )
        self.assertEqual("门在你面前打开。", result["text"])
        prompt = call.call_args.kwargs["messages"][0]["content"]
        self.assertIn("门已打开", prompt)


if __name__ == "__main__":
    unittest.main()
