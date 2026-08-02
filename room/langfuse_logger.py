"""
Langfuse 日志集成 - v4 API

精简版：只记录三类信息
1. Room 进出的消息（trace input/output）
2. Room 当前状态机（beat/scene/resolve_gate）
3. 每个 agent 的输入输出（log_generation）
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
    """Open a root Room span: 记录 Room 进出的消息。"""
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
    """Close the root Room span: 记录 Room 最终输出消息。"""
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
    """Nested span for Room phases. 精简后只保留 room.tick 这一层。"""
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
    except Exception:
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
    """记录 Room 状态机关键节点。

    精简后只记录以下事件（其余静默跳过）：
    - room.tick_enter / room.tick_exit: tick 进出
    - room.directive: director DIRECT 输出
    - room.resolution: director RESOLVE 输出
    - room.resolve_gate: 是否走 fast path / full path
    - room.publish: 最终发布的消息
    - room.recovery_auto_advance: 状态机自动推进
    - room.scene_location_from_narrator: 场景地点变更
    """
    _ALLOWED = {
        "room.tick_enter",
        "room.tick_exit",
        "room.directive",
        "room.resolution",
        "room.resolve_gate",
        "room.publish",
        "room.recovery_auto_advance",
        "room.scene_location_from_narrator",
        "room.final_state",
        "room.trace_start",
        "room.trace_end",
    }
    if name not in _ALLOWED:
        return

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
    """错误日志：始终记录，不受 _ALLOWED 过滤。"""
    if not _ENABLED:
        return
    lf = _get_client()
    if lf is None:
        return

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

    try:
        from langfuse import propagate_attributes

        meta = ctx.base_metadata()
        meta["error"] = message[:300]
        with propagate_attributes(session_id=ctx.session_id, user_id=ctx.user_id):
            with lf.start_as_current_observation(
                as_type="span",
                name=name,
                input=_to_io(input_data) if input_data else {"status": "error"},
                output=_to_io(payload),
                metadata=meta,
                level="ERROR",
            ):
                pass
    except Exception:
        pass


def log_generation(
    ctx: LangfuseCtx,
    agent_id: str,
    system: str = "",
    messages: list | None = None,
    output: str = "",
    duration_ms: float = 0,
    metadata: dict[str, Any] | None = None,
):
    """记录每个 agent 的 LLM 调用输入输出。"""
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

        meta = {
            **ctx.base_metadata(),
            "duration_ms": duration_ms,
            "content_len": len(output or ""),
            "empty_content": not bool((output or "").strip()),
        }
        if metadata:
            meta.update(metadata)
        level = "ERROR" if meta.get("empty_content") else "DEFAULT"
        out = _truncate(output) if (output or "").strip() else {
            "status": "empty_content",
            "finish_reason": meta.get("finish_reason"),
            "usage": meta.get("usage"),
            "raw_choice": meta.get("raw_choice"),
        }

        with propagate_attributes(session_id=ctx.session_id, user_id=ctx.user_id):
            with lf.start_as_current_observation(
                as_type="span",
                name=agent_id,
                input=inp,
                output=out,
                metadata=meta,
                level=level,
            ) as span:
                with span.start_as_current_observation(
                    as_type="generation",
                    name=f"{agent_id}.llm",
                    model=str(meta.get("model") or "deepseek-v4-flash"),
                    input=inp,
                    output=out,
                    metadata=meta,
                    level=level,
                ):
                    pass
    except Exception:
        pass


def log_state_change(
    ctx: LangfuseCtx, key: str, value: Any, source: str = "room"
):
    """状态变更：精简后静默跳过，state_changes 已在 room.resolution 里记录。"""
    return


def flush(ctx: LangfuseCtx | None = None):
    if not _ENABLED:
        return
    lf = _get_client()
    if lf:
        try:
            lf.flush()
        except Exception:
            pass
