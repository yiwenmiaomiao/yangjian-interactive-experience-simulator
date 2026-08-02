from __future__ import annotations

import unittest
from unittest.mock import patch

from room import contracts, director
from tests.contract_fixtures import task


class EnvelopeTests(unittest.TestCase):
    def test_message_wraps_typed_payload_with_runtime_identity(self) -> None:
        payload = contracts.YangJianTurnInput(
            task=task(),
            scene={"id": "m1"},
            public_room_history=(),
        )
        message = contracts.new_message(
            turn_id="turn_1",
            story_id="story_1",
            beat_id="m1",
            phase=contracts.Phase.ACT,
            sender=contracts.AgentRef(
                agent_id="room", kind=contracts.AgentKind.ROOM
            ),
            recipient=contracts.AgentRef(
                agent_id="yangjian", kind=contracts.AgentKind.ACTOR
            ),
            message_type="yangjian.turn.input",
            payload=payload,
        )
        raw = contracts.to_dict(message)
        self.assertEqual("1.0", raw["schema_version"])
        self.assertEqual("ACT", raw["phase"])
        self.assertEqual("task_yangjian", raw["payload"]["task"]["task_id"])

    def test_director_endpoint_returns_typed_envelope(self) -> None:
        request = contracts.new_message(
            turn_id="turn_1",
            story_id="story_1",
            beat_id="m1",
            phase=contracts.Phase.DIRECT,
            sender=contracts.AgentRef(
                agent_id="room", kind=contracts.AgentKind.ROOM
            ),
            recipient=contracts.AgentRef(
                agent_id="director", kind=contracts.AgentKind.DIRECTOR
            ),
            message_type="director.direct.input",
            payload=contracts.DirectorDirectInput(
                user_event={"text": "继续"},
                story_cursor={"beat_id": "m1"},
                world_snapshot={},
                available_actor_agents=(),
            ),
        )
        raw_directive = {
            "directive_id": "directive_1",
            "observed_user_intent": {
                "intent": "continue",
                "confidence": 1.0,
            },
            "actor_tasks": [{
                "task_id": "task_yangjian",
                "target_agent_id": "yangjian",
                "objective": "回应当前局面",
                "source_reference": "m1",
            }],
            "npc_commands": [],
            "desired_progress": "maintain",
            "narration_request": None,
            "fallback_world_event": None,
        }
        with patch.object(
            director, "decide_direct", return_value=raw_directive
        ):
            response = director.handle_direct(request)
        self.assertIsInstance(
            response.payload, contracts.DirectorDirective
        )
        self.assertEqual(request.message_id, response.correlation_id)
        self.assertEqual(contracts.Phase.DIRECT, response.phase)

    def test_resolve_endpoint_preserves_decision_and_continuation(self) -> None:
        proposal = contracts.ActorProposal(
            proposal_id="proposal_1",
            task_id="task_yangjian",
            agent_id="yangjian",
            intent="respond",
            dialogue=contracts.DialogueProposal(text="知道了。"),
        )
        actor_result = contracts.ActorTurnResult(
            result_id="proposal_1",
            task_id="task_yangjian",
            agent_id="yangjian",
            kind=contracts.ActorResultKind.PROPOSAL,
            proposal=proposal,
        )
        request = contracts.new_message(
            turn_id="turn_1",
            story_id="story_1",
            beat_id="m1",
            phase=contracts.Phase.RESOLVE,
            sender=contracts.AgentRef(
                agent_id="room", kind=contracts.AgentKind.ROOM
            ),
            recipient=contracts.AgentRef(
                agent_id="director", kind=contracts.AgentKind.DIRECTOR
            ),
            message_type="director.resolve.input",
            payload=contracts.DirectorResolveInput(
                directive_id="directive_1",
                story_cursor={"beat_id": "m1"},
                world_snapshot={},
                actor_results=(actor_result,),
            ),
        )
        raw_resolution = {
            "resolution_id": "resolution_1",
            "decisions": [{
                "proposal_id": "proposal_1",
                "result": "accept",
                "outcome_summary": "杨戬回应了用户",
                "final_dialogue": {"text": "知道了。"},
                "final_action": None,
            }],
            "state_changes": [],
            "next_beat": None,
            "continuation": {
                "kind": "continue_current",
                "reason": "等待用户下一次互动",
                "target_id": None,
                "world_event": None,
            },
        }
        with patch.object(
            director, "decide_resolve", return_value=raw_resolution
        ):
            response = director.handle_resolve(request)
        self.assertIsInstance(
            response.payload, contracts.DirectorResolution
        )
        self.assertEqual(
            "杨戬回应了用户",
            response.payload.decisions[0].outcome_summary,
        )
        self.assertEqual(
            "continue_current", response.payload.continuation.kind
        )


if __name__ == "__main__":
    unittest.main()
