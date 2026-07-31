from __future__ import annotations

import unittest

from agent_schemas import (
    ActorTurnOutput,
    DirectorDirectiveOutput,
    NarrationOutput,
)
from agent_schemas.actor import ActorAbstentionOutput, ActorProposalOutput
from agent_schemas.director import (
    DirectorNarrationOutput,
    ObservedUserIntentOutput,
    ResolveGateOutput,
    UserTurnDisclosureOutput,
    UserTurnOutput,
)
from director_control.schemas import DIRECTIVE_SCHEMA, RESOLUTION_SCHEMA


class AgentSchemaTests(unittest.TestCase):
    def test_directive_schema_is_generated_from_pydantic(self) -> None:
        self.assertEqual("object", DIRECTIVE_SCHEMA["type"])
        self.assertIn("tasks", DIRECTIVE_SCHEMA["properties"])

    def test_actor_turn_output_validates_proposal(self) -> None:
        payload = ActorTurnOutput(
            result_type="proposal",
            proposal=ActorProposalOutput(intent="respond"),
        )
        self.assertEqual("proposal", payload.result_type)

    def test_actor_turn_output_validates_abstention(self) -> None:
        payload = ActorTurnOutput(
            result_type="abstain",
            abstention=ActorAbstentionOutput(
                reason_code="NEEDS_CONTEXT",
                reason="No grounded action",
            ),
        )
        self.assertEqual("abstain", payload.result_type)

    def test_directive_output_accepts_minimal_valid_payload(self) -> None:
        payload = DirectorDirectiveOutput(
            observed_user_intent=ObservedUserIntentOutput(
                intent="continue",
            ),
            user_turn=UserTurnOutput(
                kind="dialogue",
                disclosure=UserTurnDisclosureOutput(
                    required=False,
                ),
            ),
            resolve_gate=ResolveGateOutput(
                required=True,
                act_required=True,
            ),
            narration=DirectorNarrationOutput(
                required=False,
                purpose="none",
                timing="none",
            ),
        )
        # mode/chapter/beat/desired_progress etc. were removed from
        # LLM output schema; Room fills them from beat_info.
        # Just verify the model validates.
        self.assertEqual("dialogue", payload.user_turn.kind)

    def test_narration_output_allows_empty_text(self) -> None:
        self.assertEqual("", NarrationOutput().text)

    def test_resolution_schema_is_generated_from_pydantic(self) -> None:
        self.assertEqual("object", RESOLUTION_SCHEMA["type"])
        self.assertIn("user_outcome", RESOLUTION_SCHEMA["properties"])


if __name__ == "__main__":
    unittest.main()
