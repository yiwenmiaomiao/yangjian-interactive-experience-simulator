"""LLM-backed NPC Agent runtime implementing the NPCRuntime port."""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_schemas import NPCTurnOutput, StructuredOutputError, call_structured
from npc_manager import (
    NPC_BASE_SYSTEM_PROMPT,
    NPCProposal,
    NPCTurnContext,
    build_npc_turn_input_json,
)


class NPCAbstention(Exception):
    """Raised when the NPC Agent intentionally abstains from acting."""

    def __init__(self, abstention: dict[str, Any]) -> None:
        super().__init__(abstention.get("reason", "NPC abstained"))
        self.abstention = abstention


class LLMNPCRuntime:
    """Run one NPC turn via LLM and return a structured NPCProposal."""

    def run_turn(self, context: NPCTurnContext) -> NPCProposal:
        npc_input = build_npc_turn_input_json(context)
        try:
            parsed = call_structured(
                NPCTurnOutput,
                agent_id=f"npc/{context.npc_id}",
                system=NPC_BASE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": npc_input}],
                temperature=0.7,
                max_tokens=800,
            )
        except StructuredOutputError as exc:
            raise NPCAbstention({
                "request_id": f"abstain_{context.npc_id}_{context.task.task_id}",
                "task_id": context.task.task_id,
                "agent_id": context.npc_id,
                "reason_code": "INVALID_OUTPUT",
                "reason": str(exc),
                "blocked_by": (),
                "suggested_condition": "",
            }) from exc

        if parsed.result_type == "abstain":
            data = parsed.abstention
            raise NPCAbstention({
                "request_id": (
                    f"abstain_{context.npc_id}_{context.task.task_id}"
                ),
                "task_id": context.task.task_id,
                "agent_id": context.npc_id,
                "reason_code": str(
                    data.reason_code if data else "INSUFFICIENT_CONTEXT"
                ),
                "reason": str(
                    data.reason if data else "NPC cannot act consistently"
                ),
                "blocked_by": tuple(data.blocked_by if data else ()),
                "suggested_condition": str(
                    data.suggested_condition if data else ""
                ),
            })

        proposal = parsed.proposal
        if proposal is None:
            raise NPCAbstention({
                "request_id": (
                    f"abstain_{context.npc_id}_{context.task.task_id}"
                ),
                "task_id": context.task.task_id,
                "agent_id": context.npc_id,
                "reason_code": "EMPTY_RESPONSE",
                "reason": "NPC did not provide a proposal",
                "blocked_by": (),
                "suggested_condition": "",
            })
        return NPCProposal(
            npc_id=context.npc_id,
            intent=proposal.intent,
            utterance=proposal.utterance,
            action=proposal.action,
            proposed_effects=tuple(proposal.proposed_effects),
            proactive=proposal.proactive,
        )
