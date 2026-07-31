"""Per-user/thread runtime path selection for Room persistence."""

from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token


_IDENTITY = ContextVar(
    "yangjian_runtime_identity",
    default=("default", "default"),
)


def set_identity(user_id: str, thread_id: str) -> Token:
    return _IDENTITY.set((_safe(user_id), _safe(thread_id)))


def reset_identity(token: Token) -> None:
    _IDENTITY.reset(token)


def current_identity() -> tuple[str, str]:
    return _IDENTITY.get()


def scoped_path(default_path: str) -> str:
    user_id, thread_id = current_identity()
    if (user_id, thread_id) == ("default", "default"):
        return default_path
    base = os.path.dirname(default_path)
    filename = os.path.basename(default_path)
    directory = os.path.join(base, "story_runs", user_id, thread_id)
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, filename)


@contextmanager
def process_lock(path: str, timeout: float = 120.0):
    """Serialize Room ticks across gateway/poller processes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+b")
    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    deadline = time.monotonic() + timeout
    try:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Timed out waiting for Room process lock")
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _safe(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value or "default")
    return normalized[:80] or "default"
