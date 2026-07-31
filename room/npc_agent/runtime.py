"""LLM-backed NPC Agent runtime implementing the NPCRuntime port."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm
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
        raw = llm.call(
            agent_id=f"npc/{context.npc_id}",
            system=NPC_BASE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": npc_input}],
            temperature=0.7,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        parsed = _parse_json_object(raw)
        if parsed.get("result_type") == "abstain":
            data = parsed.get("abstention", parsed)
            raise NPCAbstention({
                "request_id": str(
                    data.get("request_id")
                    or f"abstain_{context.npc_id}_{context.task.task_id}"
                ),
                "task_id": context.task.task_id,
                "agent_id": context.npc_id,
                "reason_code": str(
                    data.get("reason_code") or "INSUFFICIENT_CONTEXT"
                ),
                "reason": str(
                    data.get("reason") or "NPC cannot act consistently"
                ),
                "blocked_by": tuple(data.get("blocked_by", ())),
                "suggested_condition": str(
                    data.get("suggested_condition", "")
                ),
            })
        return _parse_proposal(raw, context.npc_id)


def _parse_proposal(raw: str, npc_id: str) -> NPCProposal:
    text = _extract_json(raw)
    try:
        data = json.loads(text)
        if data.get("result_type") == "proposal":
            data = data.get("proposal", {})
        return NPCProposal(
            npc_id=npc_id,
            intent=data.get("intent", ""),
            utterance=data.get("utterance", ""),
            action=data.get("action", ""),
            proposed_effects=tuple(data.get("proposed_effects", [])),
            proactive=bool(data.get("proactive", False)),
        )
    except (json.JSONDecodeError, TypeError):
        return NPCProposal(
            npc_id=npc_id,
            intent="respond",
            utterance=text,
            action="",
            proposed_effects=(),
        )


def _extract_json(raw: str) -> str:
    text = raw.strip()
    for prefix in ("```json", "```"):
        if prefix in text:
            text = text.split(prefix, 1)[1]
            text = text.rsplit("```", 1)[0]
            break
    return text.strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(_extract_json(raw))
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}
