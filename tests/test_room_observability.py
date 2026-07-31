from __future__ import annotations

import unittest
from unittest.mock import patch

from room import deliver
from room import photon_room_bridge as bridge


class DeliverRoleTests(unittest.TestCase):
    def test_system_and_npc_roles_are_deliverable(self) -> None:
        self.assertEqual("【系统】", deliver._role_prefix("系统"))
        self.assertEqual("【npc_guard】", deliver._role_prefix("npc_guard"))
        self.assertEqual("【守卫的动作】", deliver._role_prefix("守卫的动作"))

    def test_handle_and_deliver_sends_error_outputs(self) -> None:
        with (
            patch.object(
                bridge,
                "handle_message",
                return_value={
                    "ok": False,
                    "error": "story_plan_not_active",
                    "output": [{
                        "role": "系统",
                        "text": "【故事计划未激活】",
                    }],
                },
            ),
            patch.object(
                bridge, "deliver_outputs", return_value=(1, 0)
            ) as deliver_mock,
        ):
            result = bridge.handle_and_deliver("hello")
        deliver_mock.assert_called_once()
        self.assertEqual(1, result["delivery"]["sent"])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
