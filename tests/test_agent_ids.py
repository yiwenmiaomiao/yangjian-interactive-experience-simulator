from __future__ import annotations

import unittest

from agent_ids import (
    build_available_actor_pool,
    coerce_target_in_pool,
    display_agent_name,
    is_yangjian,
    normalize_agent_id,
    reset_available_targets,
    set_available_targets,
)
from room.director import _sanitize_canonical_directive
from room.director_control import validate_directive
from tests.test_guard import context, directive


class AgentIdAliasTests(unittest.TestCase):
    def test_normalize_chinese_display_name(self) -> None:
        self.assertEqual("yangjian", normalize_agent_id("杨戬"))
        self.assertEqual("yangjian", normalize_agent_id("yangjian"))
        self.assertTrue(is_yangjian("杨戬"))

    def test_display_name_for_users(self) -> None:
        self.assertEqual("杨戬", display_agent_name("yangjian"))
        self.assertEqual("杨戬", display_agent_name("杨戬"))

    def test_build_pool_from_beat_info(self) -> None:
        pool = build_available_actor_pool({
            "active_npcs": ["xiaotian"],
            "npc_profiles": [{"profile_id": "profile_guard"}],
        })
        self.assertEqual(("profile_guard", "xiaotian", "yangjian"), pool)

    def test_pool_rejects_unknown_target(self) -> None:
        token = set_available_targets(["yangjian"])
        try:
            with self.assertRaises(ValueError):
                coerce_target_in_pool("unknown_npc")
            self.assertEqual("yangjian", coerce_target_in_pool("杨戬"))
        finally:
            reset_available_targets(token)

    def test_pydantic_normalizes_task_target(self) -> None:
        from agent_schemas.director import (
            DirectorNarrationOutput,
            DirectorTaskOutput,
            ObservedUserIntentOutput,
            ResolveGateOutput,
            UserTurnDisclosureOutput,
            UserTurnOutput,
            DirectorDirectiveOutput,
        )

        token = set_available_targets(["yangjian"])
        try:
            task = DirectorTaskOutput(
                target="杨戬",
                objective="回应",
            )
            self.assertEqual("yangjian", task.target)

            turn = UserTurnOutput(
                kind="dialogue",
                disclosure=UserTurnDisclosureOutput(required=False),
            )

            payload = DirectorDirectiveOutput(
                observed_user_intent=ObservedUserIntentOutput(
                    intent="continue"
                ),
                user_turn=turn,
                resolve_gate=ResolveGateOutput(
                    required=True, act_required=True
                ),
                tasks=[task],
                narration=DirectorNarrationOutput(
                    required=False,
                    purpose="none",
                    timing="none",
                ),
            )
            self.assertEqual("yangjian", payload.tasks[0].target)
        finally:
            reset_available_targets(token)

    def test_sanitize_rewrites_chinese_task_target(self) -> None:
        payload = directive()
        payload["tasks"][0]["target"] = "杨戬"
        payload["user_turn"]["target"] = "杨戬"
        sanitized = _sanitize_canonical_directive(payload, {})
        self.assertEqual("yangjian", sanitized["tasks"][0]["target"])
        self.assertEqual("yangjian", sanitized["user_turn"]["target"])
        report = validate_directive(sanitized, context())
        self.assertTrue(report.is_valid, report.issues)

    def test_schema_hint_includes_target_enum(self) -> None:
        from agent_schemas.director import DirectorDirectiveOutput
        from agent_schemas.structured_llm import schema_to_json_object_format

        _, hint = schema_to_json_object_format(
            DirectorDirectiveOutput,
            target_pool=("yangjian", "npc_a"),
        )
        self.assertIn('"enum":["yangjian","npc_a"]', hint.replace(" ", ""))
        self.assertIn("可调度 target 池", hint)


if __name__ == "__main__":
    unittest.main()
