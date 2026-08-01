#!/usr/bin/env python3
"""Photon → Room 自动桥接"""
import os, sys, json, time, traceback

ROOM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "room")
sys.path.insert(0, ROOM_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __package__:
    from .room import tick, format_output
    from . import room as room_mod
    import story_state as ss, director
    import runtime_context
    from deliver import deliver_outputs
else:
    from room import tick, format_output
    import room as room_mod
    import story_state as ss, director
    import runtime_context
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


def handle_message(
    user_message: str,
    *,
    user_id: str = "default",
    thread_id: str = "default",
    close_trace: bool = True,
    lf_ctx=None,
) -> dict:
    from langfuse_logger import (
        LangfuseCtx,
        start_room_trace,
        end_room_trace,
        log_event,
        log_error,
        room_phase,
    )

    # # 前缀 = 提示请求：不走 Room 主循环，直接生成方向提示
    if user_message and str(user_message).strip().startswith("#"):
        import hint as hint_mod
        result = hint_mod.generate_hint(user_id=user_id, thread_id=thread_id)
        return result

    token = runtime_context.set_identity(user_id, thread_id)
    owns_trace = False
    if lf_ctx is None:
        lf_ctx = LangfuseCtx(
            tick=0,
            user_id=user_id,
            thread_id=thread_id,
            source="photon",
            turn_id=f"ingress_{int(time.time())}",
            user_message=user_message,
        )
        start_room_trace(
            lf_ctx,
            name="room.ingress",
            input_data={
                "user_message": user_message,
                "user_id": user_id,
                "thread_id": thread_id,
            },
        )
        owns_trace = True
    try:
        with room_phase(lf_ctx, "room.ensure_story_active"):
            story_ok = ensure_story_active()
            log_event(
                lf_ctx,
                "room.story_status",
                output_data={
                    "ensure_story_active": story_ok,
                    "has_beat_cache": bool(director._CACHED_BEAT_INFO),
                    "story_plan_active_flag": bool(room_mod._story_plan_active),
                },
                level="DEFAULT" if story_ok else "WARNING",
            )
        if not director._CACHED_BEAT_INFO:
            bi = ss.get_current_beat_info(ss.load_state())
            director.set_story_context(bi)
            log_event(
                lf_ctx,
                "room.beat_context_loaded",
                output_data={
                    "beat_id": bi.get("current_beat_id") if isinstance(bi, dict) else None,
                    "error": bi.get("error") if isinstance(bi, dict) else None,
                },
            )
        with room_phase(
            lf_ctx,
            "room.tick",
            input_data={"user_message": user_message, "source": "user"},
        ):
            result = tick(
                user_message=user_message,
                source="user",
                user_id=user_id,
                thread_id=thread_id,
            )
        log_event(
            lf_ctx,
            "room.tick_result",
            output_data={
                "ok": result.get("ok"),
                "error": result.get("error"),
                "output_count": len(result.get("output") or []),
                "roles": [
                    item.get("role") for item in (result.get("output") or [])
                ],
                "resolve_gate": (result.get("directive") or {}).get(
                    "resolve_gate"
                ),
            },
            level="DEFAULT" if result.get("ok") else "ERROR",
        )
        if owns_trace and close_trace:
            end_room_trace(
                lf_ctx,
                output_data={
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                    "output": result.get("output"),
                },
                level="DEFAULT" if result.get("ok") else "ERROR",
                status_message=str(result.get("error") or "ok"),
            )
        result["_lf_ctx"] = lf_ctx
        return result
    except Exception as exc:
        log_error(lf_ctx, "room.ingress_exception", exc, input_data=user_message)
        if owns_trace and close_trace:
            end_room_trace(
                lf_ctx,
                output_data={"ok": False, "error": str(exc)},
                level="ERROR",
                status_message=str(exc),
            )
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(exc),
            "output": [{"role": "系统", "text": f"【Room 入口异常: {exc}】"}],
            "_lf_ctx": lf_ctx,
        }
    finally:
        runtime_context.reset_identity(token)


def handle_and_deliver(
    user_message: str,
    *,
    user_id: str = "default",
    thread_id: str = "default",
    delay: float = 3.0,
) -> dict:
    """Deterministic ingress entrypoint: process once, then deliver once."""
    from langfuse_logger import (
        LangfuseCtx,
        start_room_trace,
        end_room_trace,
        log_event,
        log_error,
        room_phase,
    )

    lf_ctx = LangfuseCtx(
        tick=0,
        user_id=user_id,
        thread_id=thread_id,
        source="photon",
        turn_id=f"ingress_{int(time.time())}",
        user_message=user_message,
    )
    start_room_trace(
        lf_ctx,
        name="room.ingress",
        input_data={
            "user_message": user_message,
            "user_id": user_id,
            "thread_id": thread_id,
        },
    )
    result: dict = {"ok": False, "output": []}
    try:
        result = handle_message(
            user_message,
            user_id=user_id,
            thread_id=thread_id,
            close_trace=False,
            lf_ctx=lf_ctx,
        )
        outputs = list(result.get("output") or [])
        # Deliver while the ingress root is still open so deliver spans nest.
        try:
            with room_phase(
                lf_ctx,
                "room.deliver",
                input_data={
                    "ok": result.get("ok"),
                    "error": result.get("error"),
                    "output_count": len(outputs),
                    "roles": [item.get("role") for item in outputs],
                },
            ):
                sent, skipped = deliver_outputs(
                    outputs, delay=delay, lf_ctx=lf_ctx
                )
            result["delivery"] = {"sent": sent, "skipped": skipped}
            log_event(
                lf_ctx,
                "room.deliver_result",
                output_data=result["delivery"],
                level="WARNING" if sent == 0 and outputs else "DEFAULT",
            )
        except Exception as exc:
            log_error(lf_ctx, "room.deliver_exception", exc)
            result["delivery"] = {
                "sent": 0,
                "skipped": len(outputs),
                "error": str(exc),
            }
        return result
    finally:
        end_room_trace(
            lf_ctx,
            output_data={
                "ok": result.get("ok"),
                "error": result.get("error"),
                "delivery": result.get("delivery"),
                "output": result.get("output"),
            },
            level="DEFAULT" if result.get("ok") else "ERROR",
            status_message=str(result.get("error") or "ok"),
        )
        result.pop("_lf_ctx", None)


def process_stdin():
    msg = sys.stdin.read().strip()
    if not msg:
        return
    result = handle_and_deliver(msg)
    if result.get("ok"):
        delivery = result.get("delivery", {})
        sent = delivery.get("sent", 0)
        skipped = delivery.get("skipped", 0)
        print(f"sent={sent} skipped={skipped}", file=sys.stderr)
    else:
        print(f"tick_error={result.get('error','')}", file=sys.stderr)
        delivery = result.get("delivery", {})
        print(
            f"deliver_sent={delivery.get('sent', 0)} "
            f"skipped={delivery.get('skipped', 0)}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        result = handle_message(sys.argv[1])
        result.pop("_lf_ctx", None)
        if result.get("ok"):
            print(format_output(result))
        else:
            print(f"Error: {result.get('error','')}", file=sys.stderr)
    else:
        process_stdin()
