"""Structured LLM calls validated by Pydantic models."""

from __future__ import annotations

import json
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

import llm
from agent_ids import get_available_targets

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


def _inject_target_pool_enum(schema: dict[str, Any], pool: Sequence[str]) -> dict[str, Any]:
    """Patch JSON Schema so tasks.target / user_turn.target show an enum pool."""
    values = [str(item) for item in pool if item]
    if not values:
        return schema

    def patch_target_props(node: Any) -> None:
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict) and "target" in props and isinstance(
            props["target"], dict
        ):
            target = dict(props["target"])
            target["enum"] = values
            target["description"] = (
                "Must be an exact agent_id from the current target pool: "
                + ", ".join(values)
            )
            props["target"] = target
        for child in node.values():
            if isinstance(child, dict):
                patch_target_props(child)
            elif isinstance(child, list):
                for item in child:
                    patch_target_props(item)

    patched = json.loads(json.dumps(schema))
    patch_target_props(patched)
    return patched


def schema_to_json_object_format(
    model: type[BaseModel],
    *,
    target_pool: Sequence[str] | None = None,
) -> tuple[dict, str]:
    """Convert a Pydantic model into DeepSeek-compatible response_format + hint.

    DeepSeek ``deepseek-v4-flash`` rejects ``response_format.type=json_schema``.
    We keep ``{"type": "json_object"}`` on the wire and put the JSON Schema into
    the prompt so the model still targets the right shape; Pydantic validates.
    """
    schema = model.model_json_schema()
    pool = list(target_pool) if target_pool is not None else sorted(
        get_available_targets()
    )
    schema = _inject_target_pool_enum(schema, pool)
    response_format = {"type": "json_object"}
    pool_line = (
        f"\n本回合可调度 target 池（tasks.target / user_turn.target 只能从这里选）："
        f"{json.dumps(pool, ensure_ascii=False)}"
        if pool
        else ""
    )
    hint = (
        "你必须只输出一个 JSON 对象（不要 markdown 代码块，不要额外说明），"
        f"并尽量符合以下 JSON Schema（模型名 {model.__name__}）："
        f"{pool_line}\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    return response_format, hint


def call_structured(
    model: type[T],
    *,
    agent_id: str,
    system: str,
    messages: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2000,
    max_retries: int = 2,
    target_pool: Sequence[str] | None = None,
) -> T:
    """Call the LLM and validate the response with a Pydantic output model."""
    response_format, schema_hint = schema_to_json_object_format(
        model, target_pool=target_pool
    )
    system_with_schema = f"{system.rstrip()}\n\n{schema_hint}"
    attempt_messages = list(messages)
    last_error: ValidationError | None = None

    for attempt in range(max_retries + 1):
        raw = llm.call(
            agent_id=agent_id,
            system=system_with_schema,
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
                    "content": "上一次调用失败，请重新输出符合要求的 JSON 对象。",
                }
            ]
            continue

        try:
            return model.model_validate_json(extract_json_text(raw))
        except ValidationError as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            pool = list(target_pool) if target_pool is not None else sorted(
                get_available_targets()
            )
            attempt_messages = attempt_messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "输出未通过结构校验，请只返回一个 JSON 对象。"
                        f" 问题：{json.dumps(exc.errors()[:5], ensure_ascii=False)}。"
                        f" tasks.target 只能从当前池选择：{json.dumps(pool, ensure_ascii=False)}"
                    ),
                },
            ]

    raise StructuredOutputError(
        f"{agent_id} output failed schema validation"
        + (f": {last_error}" if last_error is not None else "")
    )
