#!/usr/bin/env python3
"""Photon → Room 自动桥接"""
import os, sys, json, time

ROOM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "room")
sys.path.insert(0, ROOM_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __package__:
    from .room import tick, format_output
    from . import room as room_mod
    from . import story_state as ss, director
    from .deliver import deliver_outputs
else:
    from room import tick, format_output
    import room as room_mod
    import story_state as ss, director
    from deliver import deliver_outputs


def ensure_story_active():
    plan = ss.get_plan()
    if not plan:
        plan_path = ss.DEFAULT_PLAN_PATH
        if os.path.exists(plan_path):
            plan = ss.load_plan(plan_path)

    if not plan:
        return False

    persisted_state = ss.load_state()
    if persisted_state.get("status") == "active":
        beat_info = ss.get_current_beat_info(persisted_state)
        if beat_info.get("error"):
            return False
        director.set_story_context(beat_info)
        room_mod._story_plan_active = True
        return True

    # Only a pristine inactive state starts from the first beat. Completed or
    # otherwise progressed stories must never be reset implicitly on startup.
    if (
        persisted_state.get("status") == "inactive"
        and not persisted_state.get("current_beat_id")
        and not persisted_state.get("completed_beats")
    ):
        state = ss.activate_plan()
        director.set_story_context(ss.get_current_beat_info(state))
        room_mod._story_plan_active = True
        return True

    return False


def handle_message(user_message: str) -> dict:
    ensure_story_active()
    from director import _CACHED_BEAT_INFO
    if not _CACHED_BEAT_INFO:
        bi = ss.get_current_beat_info(ss.load_state())
        director.set_story_context(bi)
    result = tick(user_message=user_message, source="user")
    return result


def process_stdin():
    msg = sys.stdin.read().strip()
    if not msg:
        return
    result = handle_message(msg)
    if result.get("ok"):
        sent, skipped = deliver_outputs(result.get("output", []))
        print(f"sent={sent} skipped={skipped}", file=sys.stderr)
    else:
        print(f"tick_error={result.get('error','')}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        result = handle_message(sys.argv[1])
        if result.get("ok"):
            print(format_output(result))
        else:
            print(f"Error: {result.get('error','')}", file=sys.stderr)
    else:
        process_stdin()
