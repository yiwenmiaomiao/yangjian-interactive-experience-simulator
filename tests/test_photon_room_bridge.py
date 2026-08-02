import unittest
from unittest.mock import Mock, patch

from room import photon_room_bridge as bridge
from room import room as room_module


class EnsureStoryActiveTests(unittest.TestCase):
    def setUp(self) -> None:
        room_module._story_plan_active = False

    def tearDown(self) -> None:
        room_module._story_plan_active = False

    def test_cold_start_restores_active_story_without_resetting_progress(self) -> None:
        persisted = {
            "status": "active",
            "current_beat_id": "m3",
            "completed_beats": ["m1", "m2"],
        }
        beat_info = {"current_beat_id": "m3", "beat_plot": "continue"}

        with (
            patch.object(bridge.ss, "get_plan", return_value=None),
            patch.object(bridge.os.path, "exists", return_value=True),
            patch.object(bridge.ss, "load_plan", return_value=object()),
            patch.object(bridge.ss, "load_state", return_value=persisted),
            patch.object(
                bridge.ss, "get_current_beat_info", return_value=beat_info
            ),
            patch.object(bridge.ss, "reset_state") as reset_state,
            patch.object(bridge.ss, "activate_plan") as activate_plan,
            patch.object(bridge.director, "set_story_context") as set_context,
        ):
            self.assertTrue(bridge.ensure_story_active())

        reset_state.assert_not_called()
        activate_plan.assert_not_called()
        set_context.assert_called_once_with(beat_info)
        self.assertTrue(room_module._story_plan_active)

    def test_pristine_inactive_story_starts_at_first_beat(self) -> None:
        initial = {
            "status": "inactive",
            "current_beat_id": "",
            "completed_beats": [],
        }
        activated = {"status": "active", "current_beat_id": "m1"}
        beat_info = {"current_beat_id": "m1", "beat_plot": "start"}

        with (
            patch.object(bridge.ss, "get_plan", return_value=object()),
            patch.object(bridge.ss, "load_state", return_value=initial),
            patch.object(bridge.ss, "activate_plan", return_value=activated) as activate,
            patch.object(
                bridge.ss, "get_current_beat_info", return_value=beat_info
            ),
            patch.object(bridge.director, "set_story_context") as set_context,
        ):
            self.assertTrue(bridge.ensure_story_active())

        activate.assert_called_once_with()
        set_context.assert_called_once_with(beat_info)

    def test_completed_story_is_not_restarted_implicitly(self) -> None:
        completed = {
            "status": "completed",
            "current_beat_id": "ending_a",
            "completed_beats": ["m1", "m2"],
        }

        with (
            patch.object(bridge.ss, "get_plan", return_value=object()),
            patch.object(bridge.ss, "load_state", return_value=completed),
            patch.object(bridge.ss, "reset_state") as reset_state,
            patch.object(bridge.ss, "activate_plan") as activate_plan,
            patch.object(bridge.director, "set_story_context") as set_context,
        ):
            self.assertFalse(bridge.ensure_story_active())

        reset_state.assert_not_called()
        activate_plan.assert_not_called()
        set_context.assert_not_called()
        self.assertFalse(room_module._story_plan_active)


if __name__ == "__main__":
    unittest.main()
