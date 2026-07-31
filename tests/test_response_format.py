from __future__ import annotations

import unittest
from unittest.mock import patch

import llm
from agent_schemas.structured_llm import (
    call_structured,
    schema_to_json_object_format,
)
from pydantic import BaseModel, Field


class _SampleOut(BaseModel):
    text: str = Field(default="")


class ResponseFormatConversionTests(unittest.TestCase):
    def test_normalize_json_schema_to_json_object(self) -> None:
        converted = llm.normalize_response_format(
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "DirectorDirectiveOutput",
                    "schema": {"type": "object"},
                    "strict": False,
                },
            }
        )
        self.assertEqual({"type": "json_object"}, converted)

    def test_normalize_keeps_json_object(self) -> None:
        self.assertEqual(
            {"type": "json_object"},
            llm.normalize_response_format({"type": "json_object"}),
        )

    def test_schema_helper_returns_json_object_and_hint(self) -> None:
        fmt, hint = schema_to_json_object_format(_SampleOut)
        self.assertEqual({"type": "json_object"}, fmt)
        self.assertIn("_SampleOut", hint)
        self.assertIn("JSON Schema", hint)

    def test_call_structured_sends_json_object_not_schema(self) -> None:
        captured: dict = {}

        def fake_call(**kwargs):
            captured.update(kwargs)
            return '{"text": "ok"}'

        with patch.object(llm, "call", side_effect=fake_call):
            result = call_structured(
                _SampleOut,
                agent_id="test",
                system="sys",
                messages=[{"role": "user", "content": "hi"}],
            )
        self.assertEqual("ok", result.text)
        self.assertEqual(
            {"type": "json_object"}, captured.get("response_format")
        )
        # Schema hint is now injected into the user message, not system prompt
        messages = captured.get("messages", [])
        user_content = ""
        for msg in messages:
            if msg.get("role") == "user":
                user_content = msg.get("content", "")
        self.assertIn("JSON Schema", user_content)
        # System prompt should stay clean (no schema hint)
        self.assertNotIn("JSON Schema", captured.get("system", ""))


if __name__ == "__main__":
    unittest.main()
