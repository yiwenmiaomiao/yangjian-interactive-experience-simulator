from __future__ import annotations

import unittest

from room.langfuse_logger import LangfuseCtx, build_trace_name


class TraceNameTests(unittest.TestCase):
    def test_user_message_is_trace_name(self) -> None:
        self.assertEqual(build_trace_name("打开盒子"), "打开盒子")

    def test_user_message_is_truncated(self) -> None:
        long = "a" * 250
        name = build_trace_name(long)
        self.assertTrue(name.endswith("…"))
        self.assertLessEqual(len(name), 200)

    def test_whitespace_only_is_na(self) -> None:
        self.assertEqual(build_trace_name("   \n", source="photon"), "NA")

    def test_cron_uses_job_name(self) -> None:
        self.assertEqual(
            build_trace_name(None, source="cron", job_name="yangjian-room"),
            "yangjian-room",
        )

    def test_cron_without_job_name_is_na(self) -> None:
        self.assertEqual(build_trace_name(None, source="cron"), "NA")

    def test_ctx_keeps_observation_name_separate(self) -> None:
        ctx = LangfuseCtx(source="photon", user_message="你好")
        self.assertEqual(ctx.trace_name, "你好")
        # observation name is chosen by start_room_trace(name=...), not here


if __name__ == "__main__":
    unittest.main()
