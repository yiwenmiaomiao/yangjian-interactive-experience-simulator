"""Structured LLM calls validated by Pydantic models."""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

import llm

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """Raised when an agent response cannot be parsed into its output model."""


def extract_json_text(raw: str) -> str:
    text = raw.strip()
    for prefix in ("```json", "```"):
        if prefix in text:
            text = text.split(prefix, 1)[1]
            text = text.rsplit("```", 1)[0]
            break
    return text.strip()


def _response_format_for_model(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "schema": schema,
            "strict": False,
        },
    }


def call_structured(
    model: type[T],
    *,
    agent_id: str,
    system: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    max_retries: int = 2,
) -> T:
    """Call the LLM and validate the response with a Pydantic output model."""
    attempt_messages = list(messages)
    last_error: ValidationError | None = None
    use_schema = True

    for attempt in range(max_retries + 1):
        response_format = (
            _response_format_for_model(model)
            if use_schema
            else {"type": "json_object"}
        )
        raw = llm.call(
            agent_id=agent_id,
            system=system,
            messages=attempt_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        if raw.startswith("【"):
            if attempt >= max_retries:
                raise StructuredOutputError(raw)
            attempt_messages = attempt_messages + [
                {
                    "role": "user",
                    "content": "上一次调用失败，请重新输出符合要求的 JSON。",
                }
            ]
            continue

        try:
            return model.model_validate_json(extract_json_text(raw))
        except ValidationError as exc:
            last_error = exc
            if use_schema:
                use_schema = False
                continue
            if attempt >= max_retries:
                break
            attempt_messages = attempt_messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "输出未通过结构校验，请只返回 JSON。"
                        f" 问题：{json.dumps(exc.errors()[:5], ensure_ascii=False)}"
                    ),
                },
            ]

    raise StructuredOutputError(
        f"{agent_id} output failed schema validation"
        + (f": {last_error}" if last_error is not None else "")
    )
