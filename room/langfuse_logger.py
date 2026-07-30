"""
Langfuse 日志集成 — v4 API，session 用 propagate_attributes

v4 中 session_id 必须通过 propagate_attributes() 传播，
不能直接在 start_as_current_observation 参数中设置。
"""
from __future__ import annotations

import os, json, time, datetime
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
            for line in open(env_path):
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
            except Exception:
                _ENABLED = False
        else:
            _ENABLED = False
    return _LF


def _truncate(text: str, max_len: int = 0) -> str:
    return text if text else ""


class LangfuseCtx:
    def __init__(self, tick: int = 0, story_id: str = "", beat_id: str = ""):
        self.tick = tick
        self.story_id = story_id
        self.beat_id = beat_id
        self.session_id = f"tick_{tick}"


# ── 记录 LLM Generation（核心） ────────────────────────


def log_generation(
    ctx: LangfuseCtx,
    agent_id: str,
    system: str = "",
    messages: list | None = None,
    output: str = "",
    duration_ms: float = 0,
):
    """记录一次 LLM 调用。session_id 通过 propagate_attributes 传播。"""
    if not _ENABLED:
        return
    lf = _get_client()
    if lf is None:
        return

    try:
        from langfuse import propagate_attributes

        inp = _truncate(system)
        if messages:
            for m in (messages or []):
                inp += f"\n{m.get('role','')}: {_truncate(str(m.get('content','')))}"

        with propagate_attributes(session_id=ctx.session_id):
            with lf.start_as_current_observation(
                as_type="span",
                name=agent_id,
                input=inp,
                output=_truncate(output),
                metadata={
                    "tick": ctx.tick,
                    "story_id": ctx.story_id,
                    "beat_id": ctx.beat_id,
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


# ── 记录状态变化 ──────────────────────────────────────


def log_state_change(ctx: LangfuseCtx, key: str, value: Any, source: str = "room"):
    if not _ENABLED:
        return
    lf = _get_client()
    if lf is None:
        return

    try:
        from langfuse import propagate_attributes

        with propagate_attributes(session_id=ctx.session_id):
            with lf.start_as_current_observation(
                as_type="span",
                name=f"state/{source}.{key}",
                metadata={
                    "tick": ctx.tick,
                    "story_id": ctx.story_id,
                    "beat_id": ctx.beat_id,
                    "state_key": key,
                    "state_value": str(value)[:200],
                },
            ):
                pass
    except Exception:
        pass


# ── flush ─────────────────────────────────────────────


def flush(ctx: LangfuseCtx | None = None):
    if not _ENABLED:
        return
    lf = _get_client()
    if lf:
        try:
            lf.flush()
        except Exception:
            pass
