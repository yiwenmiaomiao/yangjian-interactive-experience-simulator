"""Bridge to the project LLM module (room/llm.py) for story generation.

Consolidates the DeepSeek call so the planner and character loader share one
path: JSON-object response mode, Langfuse ``agent_id`` tagging, and a
configurable model via ``YANGJIAN_STORY_GENERATOR_LLM_MODEL``.
"""
from __future__ import annotations

import os
import sys
from typing import Any

PROJECT_DIR = os.path.abspath(
    os.path.expanduser(
        os.environ.get(
            "YANGJIAN_PROJECT_DIR",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
    )
)

_ROOM_PATH = os.path.join(PROJECT_DIR, "room")
if _ROOM_PATH not in sys.path:
    sys.path.insert(0, _ROOM_PATH)

import llm as room_llm  # noqa: E402

STORY_GENERATOR_MODEL_ENV = "YANGJIAN_STORY_GENERATOR_LLM_MODEL"


def resolve_model(model: str | None = None) -> str | None:
    """Resolve the model override: explicit arg > env > room default."""
    return model or os.environ.get(STORY_GENERATOR_MODEL_ENV) or None


def call_llm(
    *,
    system: str,
    user: str,
    agent_id: str,
    temperature: float = 0.7,
    max_tokens: int = 128000,
    model: str | None = None,
    json_mode: bool = True,
) -> str:
    """Call the project LLM with structured-output defaults.

    ``json_mode`` enables DeepSeek ``json_object`` response format so plan /
    character JSON is returned cleanly. Callers still validate with the codec.
    """
    return room_llm.call(
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        agent_id=agent_id,
        model=resolve_model(model),
        response_format={"type": "json_object"} if json_mode else None,
    )
