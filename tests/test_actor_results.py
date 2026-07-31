from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_schemas.actor import ActorProposalOutput, ActorTurnOutput, DialogueOutput
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
        proposal = ActorTurnOutput(
            result_type="proposal",
            proposal=ActorProposalOutput(
                intent="respond",
                dialogue=DialogueOutput(
                    text="我都听见了。",
                    intent="acknowledge",
                    addressee_ids=["用户"],
                ),
            ),
        )
        with (
            patch.object(yangjian, "_load_soul", return_value="SOUL"),
            patch.object(
                yangjian,
                "call_structured",
                return_value=proposal,
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

    def test_modify_without_finals_falls_back_to_proposal(self) -> None:
        actor_result = {
            "result_id": "r1",
            "task_id": "task_1",
            "agent_id": "yangjian",
            "kind": "proposal",
            "proposal": {
                "dialogue": {"text": "先看看再说", "intent": "respond"},
                "action": {"description": "抬手示意", "action_type": "act"},
            },
            "abstention": None,
        }
        outputs, _ = room._apply_actor_resolution(
            [actor_result],
            {
                "decisions": [{
                    "proposal_id": "r1",
                    "result": "modify",
                    "outcome_summary": "语气略作收敛",
                    "final_dialogue": None,
                    "final_action": None,
                }],
                "continuation": {
                    "kind": "continue_current",
                    "reason": "ok",
                },
            },
            [],
        )
        self.assertEqual(
            [
                {"role": "杨戬的动作", "kind": "action", "text": "抬手示意"},
                {"role": "杨戬", "kind": "dialogue", "text": "先看看再说"},
            ],
            [
                {"role": o["role"], "kind": o["kind"], "text": o["text"]}
                for o in outputs
            ],
        )


    def test_yangjian_prompt_uses_readable_history_not_raw_json(self) -> None:
        history = (
            contracts.PublishedMessage(
                message_id="m1",
                role="用户",
                kind="dialogue",
                text="你看里面是什么",
            ),
        )
        prompt = yangjian._build_turn_prompt(
            contracts.YangJianTurnInput(
                task=contracts.AgentTask(
                    task_id="task_yangjian",
                    target_agent_id="yangjian",
                    objective="面对古盒做出反应",
                    source_reference="m1",
                ),
                scene={"id": "m1"},
                public_room_history=history,
            )
        )
        self.assertIn("面对古盒做出反应", prompt)
        self.assertIn("用户：你看里面是什么", prompt)
        self.assertNotIn("message_id", prompt)
        self.assertNotIn("confirmed_event_ids", prompt)


if __name__ == "__main__":
    unittest.main()
