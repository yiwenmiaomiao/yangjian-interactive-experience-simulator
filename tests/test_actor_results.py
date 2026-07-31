from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from room import contracts, room, yangjian
from tests.contract_fixtures import task


class ActorResultTests(unittest.TestCase):
    def test_yangjian_receives_all_public_room_roles(self) -> None:
        history = tuple(
            contracts.PublishedMessage(
                message_id=f"m{index}",
                role=role,
                kind="dialogue",
                text=text,
            )
            for index, (role, text) in enumerate(
                (
                    ("用户", "用户公开消息"),
                    ("npc_guard", "NPC公开消息"),
                    ("旁白", "旁白公开消息"),
                )
            )
        )
        llm_result = {
            "result_type": "proposal",
            "proposal": {
                "intent": "respond",
                "dialogue": {
                    "text": "我都听见了。",
                    "intent": "acknowledge",
                    "addressee_ids": ["用户"],
                },
                "action": None,
                "proposed_effects": [],
                "confidence": 0.8,
                "referenced_fact_ids": [],
            },
        }
        with (
            patch.object(yangjian, "_load_soul", return_value="SOUL"),
            patch.object(
                yangjian.llm,
                "call",
                return_value=json.dumps(llm_result, ensure_ascii=False),
            ) as call,
        ):
            result = yangjian.act_turn(
                contracts.YangJianTurnInput(
                    task=task(),
                    scene={"id": "m1"},
                    public_room_history=history,
                )
            )
        prompt = call.call_args.kwargs["messages"][0]["content"]
        self.assertIn("用户公开消息", prompt)
        self.assertIn("NPC公开消息", prompt)
        self.assertIn("旁白公开消息", prompt)
        self.assertEqual("proposal", result["kind"])

    def test_abstention_produces_no_user_output(self) -> None:
        actor_result = {
            "result_id": "abstain_1",
            "task_id": "task_1",
            "agent_id": "yangjian",
            "kind": "abstain",
            "proposal": None,
            "abstention": {"reason": "No grounded action"},
        }
        outputs, events = room._apply_actor_resolution(
            [actor_result],
            {
                "decisions": [{
                    "proposal_id": "abstain_1",
                    "result": "accept_abstention",
                    "outcome_summary": "杨戬暂不行动",
                }],
                "continuation": {
                    "kind": "continue_current",
                    "reason": "Redispatch next turn",
                },
            },
            [],
        )
        self.assertEqual([], outputs)
        self.assertEqual([], events)


if __name__ == "__main__":
    unittest.main()
