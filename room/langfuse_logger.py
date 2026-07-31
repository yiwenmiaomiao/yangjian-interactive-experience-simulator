"""
Langfuse 日志集成 — v4 API

Room 入口级日志：只要消息打到 Room，就记录完整链路。
session_id 通过 propagate_attributes() 传播。
"""
from __future__ import annotations

import os
import json
import time
import traceback
from contextlib import contextmanager
from typing import Any

_LF = None
_ENABLED = True


def _read_env(key: str) -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
        )
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _get_client():
    global _LF, _ENABLED
    if _LF is None:
        pk = _read_env("LANGFUSE_PUBLIC_KEY")
        sk = _read_env("LANGFUSE_SECRET_KEY")
        host = _read_env("LANGFUSE_HOST") or "https://us.cloud.langfuse.com"
        if pk and sk:
            try:
                from langfuse import Langfuse
                _LF = Langfuse(public_key=pk, secret_key=sk, host=host)
                return _LF
            except ImportError:
                _ENABLED = False
                print(
                    "[langfuse] disabled: package not installed. "
                    "Run: pip install langfuse",
                    flush=True,
                )
            except Exception as exc:
                _ENABLED = False
                print(f"[langfuse] disabled: {exc}", flush=True)
        else:
            _ENABLED = False
            print(
                "[langfuse] disabled: LANGFUSE_PUBLIC_KEY / "
                "LANGFUSE_SECRET_KEY not set",
                flush=True,
            )
    return _LF


def _truncate(text: str, max_len: int = 8000) -> str:
    if not text:
        return ""
    if max_len > 0 and len(text) > max_len:
        return text[: max_len - 20] + "…[truncated]"
    return text


def safe_json(value: Any, max_len: int = 8000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return _truncate(text, max_len)


def _to_io(value: Any, *, fallback: Any = None) -> Any:
    """Langfuse input/output: prefer JSON-serializable objects, never leave unset."""
    if value is None:
        return fallback if fallback is not None else {"status": "ok"}
    try:
        # Ensure value is JSON-serializable for the Langfuse UI.
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded) > 12000:
            return {"preview": _truncate(encoded, 8000), "truncated": True}
        return json.loads(encoded)
    except Exception:
        return {"value": _truncate(str(value), 2000)}


_TRACE_NAME_MAX = 200


def build_trace_name(
    user_message: Any = None,
    *,
    source: str = "",
    job_name: str = "",
) -> str:
    """Langfuse *trace* display name (not observation name).

    Rules:
    - non-empty user message → that text (whitespace-normalized, clipped)
    - empty / whitespace-only user message → ``NA``
    - cron with no user message → scheduled job name (else ``NA``)
    - any other missing message → ``NA``
    """
    if isinstance(user_message, str):
        text = " ".join(user_message.split())
        if text:
            if len(text) > _TRACE_NAME_MAX:
                return text[: _TRACE_NAME_MAX - 1] + "…"
            return text
        return "NA"

    if source == "cron" or job_name:
        name = " ".join(str(job_name or "").split())
        return name[:_TRACE_NAME_MAX] if name else "NA"

    return "NA"


def _has_active_otel_span() -> bool:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span is None:
            return False
        ctx = span.get_span_context()
        return bool(ctx and ctx.is_valid and span.is_recording())
    except Exception:
        return False


class LangfuseCtx:
    def __init__(
        self,
        tick: int = 0,
        story_id: str = "",
        beat_id: str = "",
        *,
        user_id: str = "default",
        thread_id: str = "default",
        source: str = "",
        turn_id: str = "",
        user_message: Any = None,
        job_name: str = "",
        trace_name: str = "",
    ):
        self.tick = tick
        self.story_id = story_id
        self.beat_id = beat_id
        self.user_id = user_id
        self.thread_id = thread_id
        self.source = source
        self.turn_id = turn_id or f"turn_{tick}"
        self.user_message = user_message
        self.job_name = job_name
        self.trace_name = trace_name or build_trace_name(
            user_message, source=source, job_name=job_name
        )
        self.session_id = f"{user_id}:{thread_id}"
        self.started_at = time.time()
        self._root = None
        self._owns_root = False
        self._phase_stack: list[Any] = []

    def base_metadata(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "story_id": self.story_id,
            "beat_id": self.beat_id,
            "user_id": self.user_id,
            "thread_id": self.thread_id,
            "source": self.source,
            "turn_id": self.turn_id,
            "trace_name": self.trace_name,
            "job_name": self.job_name,
        }

    def set_user_message(self, user_message: Any) -> None:
        self.user_message = user_message
        self.trace_name = build_trace_name(
            user_message, source=self.source, job_name=self.job_name
        )


def _noop_cm():
    @contextmanager
    def _inner():
        yield None
    return _inner()


def start_room_trace(
    ctx: LangfuseCtx,
    *,
    name: str = "room.tick",
    input_data: Any = None,
    only_if_no_parent: bool = False,
):
    """Open a root Room span for one inbound message / tick.

    ``name`` is the observation/span name (unchanged operational label).
    ``ctx.trace_name`` is the Langfuse *trace* name, set via
    ``propagate_attributes(trace_name=...)``.
    """
    if not _ENABLED:
        return None
    lf = _get_client()
    if lf is None:
        return None
    if only_if_no_parent and _has_active_otel_span():
        return None
    if not ctx.trace_name:
        ctx.trace_name = build_trace_name(
            ctx.user_message, source=ctx.source, job_name=ctx.job_name
        )
    try:
        from langfuse import propagate_attributes

        attrs = propagate_attributes(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            trace_name=ctx.trace_name,
            metadata={
                "source": ctx.source,
                "turn_id": ctx.turn_id,
            },
        )
        attrs.__enter__()
        obs = lf.start_as_current_observation(
            as_type="span",
            name=name,
            input=_to_io(input_data, fallback={"status": "started"}),
            output={"status": "running"},
            metadata=ctx.base_metadata(),
        )
        root = obs.__enter__()
        ctx._root = (attrs, obs, root)
        ctx._owns_root = True
        return root
    except Exception as exc:
        print(f"[langfuse] start_room_trace failed: {exc}", flush=True)
        return None


def end_room_trace(
    ctx: LangfuseCtx,
    *,
    output_data: Any = None,
    level: str = "DEFAULT",
    status_message: str = "",
):
    """Close the root Room span and flush."""
    if ctx._root is None:
        flush(ctx)
        return
    attrs, obs, root = ctx._root
    try:
        if root is not None:
            root.update(
                output=_to_io(
                    output_data,
                    fallback={"status": status_message or "done"},
                )
            )
            meta = ctx.base_metadata()
            meta["duration_ms"] = round((time.time() - ctx.started_at) * 1000, 1)
            if status_message:
                meta["status_message"] = status_message[:500]
            root.update(metadata=meta, level=level)
        while ctx._phase_stack:
            phase = ctx._phase_stack.pop()
            try:
                phase.__exit__(None, None, None)
            except Exception:
                pass
        obs.__exit__(None, None, None)
        attrs.__exit__(None, None, None)
    except Exception as exc:
        print(f"[langfuse] end_room_trace failed: {exc}", flush=True)
    finally:
        ctx._root = None
        ctx._owns_root = False
        flush(ctx)


@contextmanager
def room_phase(ctx: LangfuseCtx, name: str, *, input_data: Any = None):
    """Nested span for Room phases (ingress / direct / act / resolve / deliver).

    Yields a mutable dict ``{"output": ...}``. Callers may set ``bag["output"]``
    before exiting; otherwise a default ``{"status": "ok"}`` is written so the
    Langfuse UI never shows output as undefined.
    """
    bag: dict[str, Any] = {"output": None}
    if not _ENABLED or _get_client() is None:
        yield bag
        return

    lf = _get_client()
    started = time.time()
    try:
        obs_cm = lf.start_as_current_observation(
            as_type="span",
            name=name,
            input=_to_io(input_data, fallback={"status": "started"}),
            output={"status": "running"},
            metadata=ctx.base_metadata(),
        )
    except Exception as exc:
        print(f"[langfuse] room_phase({name}) start failed: {exc}", flush=True)
        yield bag
        return

    with obs_cm as span:
        try:
            yield bag
        except Exception as exc:
            try:
                span.update(
                    level="ERROR",
                    output=_to_io({
                        "status": "error",
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }),
                    metadata={
                        **ctx.base_metadata(),
                        "duration_ms": round(
                            (time.time() - started) * 1000, 1
                        ),
                    },
                )
            except Exception:
                pass
            raise
        else:
            try:
                span.update(
                    output=_to_io(
                        bag.get("output"),
                        fallback={
                            "status": "ok",
                            "duration_ms": round(
                                (time.time() - started) * 1000, 1
                            ),
                        },
                    ),
                    metadata={
                        **ctx.base_metadata(),
                        "duration_ms": round(
                            (time.time() - started) * 1000, 1
                        ),
                    },
                )
            except Exception:
                pass


def log_event(
    ctx: LangfuseCtx,
    name: str,
    *,
    input_data: Any = None,
    output_data: Any = None,
    level: str = "DEFAULT",
    metadata: dict[str, Any] | None = None,
):
    """Fire-and-forget event/span for Room milestones.

    Always sets both input and output so Langfuse UI does not show undefined.
    """
    if not _ENABLED:
        return
    lf = _get_client()
    if lf is None:
        return
    try:
        from langfuse import propagate_attributes

        meta = ctx.base_metadata()
        if metadata:
            meta.update(metadata)
        # If caller only provided one side, mirror it so the UI is readable.
        if input_data is None and output_data is not None:
            input_data = {"status": "event"}
        if output_data is None and input_data is not None:
            output_data = {"status": "recorded", "echo": input_data}
        if input_data is None and output_data is None:
            input_data = {"status": "event"}
            output_data = {"status": "recorded"}

        with propagate_attributes(session_id=ctx.session_id, user_id=ctx.user_id):
            with lf.start_as_current_observation(
                as_type="span",
                name=name,
                input=_to_io(input_data),
                output=_to_io(output_data),
                metadata=meta,
                level=level,
            ):
                pass
    except Exception as exc:
        print(f"[langfuse] log_event({name}) failed: {exc}", flush=True)


def log_error(
    ctx: LangfuseCtx,
    name: str,
    error: BaseException | str,
    *,
    input_data: Any = None,
    extra: dict[str, Any] | None = None,
):
    tb = ""
    if isinstance(error, BaseException):
        tb = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        message = str(error)
    else:
        message = str(error)
    payload = {"error": message, "traceback": tb}
    if extra:
        payload["extra"] = extra
    log_event(
        ctx,
        name,
        input_data=input_data,
        output_data=payload,
        level="ERROR",
        metadata={"error": message[:300]},
    )


def log_generation(
    ctx: LangfuseCtx,
    agent_id: str,
    system: str = "",
    messages: list | None = None,
    output: str = "",
    duration_ms: float = 0,
):
    """记录一次 LLM 调用。"""
    if not _ENABLED:
        return
    lf = _get_client()
    if lf is None:
        return

    try:
        from langfuse import propagate_attributes

        inp = _truncate(system)
        if messages:
            for m in messages or []:
                inp += (
                    f"\n{m.get('role', '')}: "
                    f"{_truncate(str(m.get('content', '')))}"
                )

        with propagate_attributes(session_id=ctx.session_id, user_id=ctx.user_id):
            with lf.start_as_current_observation(
                as_type="span",
                name=agent_id,
                input=inp,
                output=_truncate(output),
                metadata={
                    **ctx.base_metadata(),
                    "duration_ms": duration_ms,
                },
            ) as span:
                with span.start_as_current_observation(
                    as_type="generation",
                    name=f"{agent_id}.llm",
                    model="deepseek-v4-flash",
                    input=inp,
                    output=_truncate(output),
                ):
                    pass
    except Exception:
        pass


def log_state_change(
    ctx: LangfuseCtx, key: str, value: Any, source: str = "room"
):
    if not _ENABLED:
        return
    lf = _get_client()
    if lf is None:
        return

    try:
        from langfuse import propagate_attributes

        with propagate_attributes(session_id=ctx.session_id, user_id=ctx.user_id):
            with lf.start_as_current_observation(
                as_type="span",
                name=f"state/{source}.{key}",
                metadata={
                    **ctx.base_metadata(),
                    "state_key": key,
                    "state_value": str(value)[:200],
                },
            ):
                pass
    except Exception:
        pass


def flush(ctx: LangfuseCtx | None = None):
    if not _ENABLED:
        return
    lf = _get_client()
    if lf:
        try:
            lf.flush()
        except Exception:
            pass
