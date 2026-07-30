from __future__ import annotations

import unittest

from room.director_control import (
    DirectorContext,
    task_signature,
    validate_directive,
    validate_resolution,
)


def context(**overrides) -> DirectorContext:
    values = {
        "chapter": "chapter_1",
        "beat": "beat_1",
        "available_agents": frozenset({"yangjian", "npc_1"}),
        "allowed_information": {
            "yangjian": frozenset({"fact_1"}),
            "npc_1": frozenset({"fact_2"}),
        },
        "allowed_source_references": frozenset({"beat_1", "side_1"}),
        "unlocked_next_beats": frozenset({"beat_2"}),
        "unlocked_side_arcs": frozenset({"side_1"}),
        "allowed_state_change_keys": frozenset({"trust", "clue_found"}),
        "proposal_ids": frozenset({"proposal_1", "proposal_2"}),
    }
    values.update(overrides)
    return DirectorContext(**values)


def directive():
    return {
        "mode": "DIRECT",
        "chapter": "chapter_1",
        "beat": "beat_1",
        "observed_user_intent": {
            "intent": "ask_question",
            "confidence": 0.8,
        },
        "tasks": [
            {
                "task_id": "task_1",
                "target": "yangjian",
                "source_reference": "beat_1",
                "objective": "回应用户的问题，但不泄露秘密",
                "information_ids": ["fact_1"],
                "success_condition": "用户获得可公开的信息",
            }
        ],
        "desired_progress": "maintain",
        "selected_side_arc": None,
        "narration": {
            "required": False,
            "purpose": "none",
            "timing": "none",
            "visible_facts": [],
            "max_characters": 0,
        },
        "hold": {
            "requested": False,
            "reason": "",
            "wait_for": "",
        },
    }


def resolution():
    return {
        "mode": "RESOLVE",
        "chapter": "chapter_1",
        "beat": "beat_1",
        "decisions": [
            {
                "proposal_id": "proposal_1",
                "result": "accept",
                "outcome_summary": "杨戬的回应成为正式事件",
            },
            {
                "proposal_id": "proposal_2",
                "result": "modify",
                "outcome_summary": "NPC的行动发生，但没有完全成功",
            },
        ],
        "state_changes": [
            {
                "key": "trust",
                "value": 1,
                "reason": "双方交换了可公开信息",
            }
        ],
        "next_beat": "beat_2",
    }


class DirectiveGuardTests(unittest.TestCase):
    def test_valid_directive_passes(self) -> None:
        self.assertTrue(validate_directive(directive(), context()).is_valid)

    def test_rejects_unauthorized_information(self) -> None:
        payload = directive()
        payload["tasks"][0]["information_ids"] = ["fact_2"]

        report = validate_directive(payload, context())

        self.assertFalse(report.is_valid)
        self.assertIn(
            "INFORMATION_NOT_ALLOWED",
            {issue.code for issue in report.issues},
        )

    def test_rejects_locked_side_arc(self) -> None:
        payload = directive()
        payload["selected_side_arc"] = "side_locked"

        report = validate_directive(payload, context())

        self.assertIn("SIDE_ARC_LOCKED", {issue.code for issue in report.issues})

    def test_rejects_idle_director_without_hold(self) -> None:
        payload = directive()
        payload["tasks"] = []

        report = validate_directive(payload, context())

        self.assertIn("DIRECTOR_IDLE", {issue.code for issue in report.issues})

    def test_rejects_consecutive_hold(self) -> None:
        payload = directive()
        payload["tasks"] = []
        payload["hold"] = {
            "requested": True,
            "reason": "等待用户回答",
            "wait_for": "用户明确回应",
        }

        report = validate_directive(
            payload,
            context(consecutive_holds=1),
        )

        self.assertIn("REPEATED_HOLD", {issue.code for issue in report.issues})

    def test_rejects_task_repeated_twice(self) -> None:
        payload = directive()
        signature = task_signature(
            "yangjian",
            payload["tasks"][0]["objective"],
        )

        report = validate_directive(
            payload,
            context(recent_task_signatures=(signature, signature)),
        )

        self.assertIn("TASK_REPEATED", {issue.code for issue in report.issues})

    def test_narration_requires_room_permission(self) -> None:
        payload = directive()
        payload["narration"] = {
            "required": True,
            "purpose": "transition",
            "timing": "before_dialogue",
            "visible_facts": ["location_changed"],
            "max_characters": 80,
        }

        report = validate_directive(payload, context())

        self.assertIn(
            "NARRATION_NOT_ALLOWED",
            {issue.code for issue in report.issues},
        )


class ResolutionGuardTests(unittest.TestCase):
    def test_valid_resolution_passes(self) -> None:
        self.assertTrue(validate_resolution(resolution(), context()).is_valid)

    def test_requires_decision_for_every_proposal(self) -> None:
        payload = resolution()
        payload["decisions"] = payload["decisions"][:1]

        report = validate_resolution(payload, context())

        self.assertIn(
            "PROPOSALS_UNDECIDED",
            {issue.code for issue in report.issues},
        )

    def test_rejects_locked_next_beat(self) -> None:
        payload = resolution()
        payload["next_beat"] = "beat_locked"

        report = validate_resolution(payload, context())

        self.assertIn("NEXT_BEAT_LOCKED", {issue.code for issue in report.issues})

    def test_rejects_unapproved_state_key(self) -> None:
        payload = resolution()
        payload["state_changes"][0]["key"] = "ending"

        report = validate_resolution(payload, context())

        self.assertIn(
            "STATE_CHANGE_NOT_ALLOWED",
            {issue.code for issue in report.issues},
        )


if __name__ == "__main__":
    unittest.main()
