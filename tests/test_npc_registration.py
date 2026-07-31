from __future__ import annotations

import unittest
from unittest.mock import patch

from room import npc_manager_runtime
from room.npc_manager import (
    InMemoryNPCRepository,
    NarrativeFunction as ManagerNarrativeFunction,
    NPCManager,
    NPCProfile,
    NPCStatus,
)
from yangjian_story_generator import (
    NarrativeFunction as StoryNarrativeFunction,
    NPCProfileSpec,
)


class NPCRegistrationTests(unittest.TestCase):
    def test_duplicate_register_is_idempotent(self) -> None:
        manager = NPCManager(
            repository=InMemoryNPCRepository(),
            profile_generator=None,
        )
        profile = NPCProfile(
            npc_id="profile_guard",
            profile_id="profile_guard",
            status=NPCStatus.READY,
            name="守门人",
            public_role="守卫",
            short_background="守在门前",
            personality=("谨慎",),
            current_goal="守住入口",
            goals=("守住入口",),
            relation_to_yangjian="敬重",
            relation_to_user="陌生",
            expression_style="简短",
            behavior_boundaries=(),
            memory_seed=(),
            profile_version=1,
        )
        first = manager.register_profile(
            profile,
            story_id="story_1",
            requirement_id="req_guard",
        )
        second = manager.register_profile(
            profile,
            story_id="story_1",
            requirement_id="req_guard",
        )
        self.assertIs(first, second)
        self.assertEqual(1, manager.metrics.generated_count)

    def test_manager_registers_complete_profile_without_generator(self) -> None:
        manager = NPCManager(
            repository=InMemoryNPCRepository(),
            profile_generator=None,
        )
        profile = NPCProfile(
            npc_id="profile_guard",
            profile_id="profile_guard",
            status=NPCStatus.READY,
            name="守门人",
            public_role="守卫",
            short_background="守在门前",
            personality=("谨慎", "尽责"),
            current_goal="守住入口",
            goals=("守住入口",),
            relation_to_yangjian="敬重",
            relation_to_user="陌生",
            expression_style="简短",
            behavior_boundaries=("不擅离岗位",),
            memory_seed=("受命守门",),
        )
        record = manager.register_profile(
            profile,
            story_id="story_1",
            requirement_id="req_guard",
        )
        self.assertEqual(("守住入口",), record.profile.goals)
        self.assertIn("profile_seed:受命守门", record.memory.important_events)

    def test_story_narrative_function_survives_manager_registration(self) -> None:
        manager = NPCManager(
            repository=InMemoryNPCRepository(),
            profile_generator=None,
        )
        spec = NPCProfileSpec(
            profile_id="profile_antagonist",
            requirement_id="req_antagonist",
            narrative_function=StoryNarrativeFunction.ANTAGONIST,
            name="拦路者",
            public_role="对手",
            personality=("强硬",),
            background="阻止主角继续前进",
            expression_style="直接",
            goals=("阻止通行",),
            relation_to_yangjian="敌对",
            relation_to_user="敌对",
        )
        with patch.object(
            npc_manager_runtime, "_get_manager", return_value=manager
        ):
            npc_manager_runtime.register_profile(
                spec, story_id="story_1"
            )
        record = manager.repository.get("profile_antagonist")
        self.assertEqual(
            (ManagerNarrativeFunction.ANTAGONIST,),
            record.profile.supported_functions,
        )


if __name__ == "__main__":
    unittest.main()
