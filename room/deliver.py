#!/usr/bin/env python3
"""
Room 输出传递器 — 逐条原样发送

room.tick() 的 outputs 已经是分开的 agent 消息。
不需要拆分，只需要：
1. 空内容跳过
2. 每条独立 hermes send
3. 间隔足够长让 iMessage 不合并
"""
import os, sys, time, subprocess

ROLE_PREFIX = {
    "旁白": "【旁白】",
    "杨戬": "【杨戬】",
    "杨戬的动作": "【杨戬的动作】",
    "系统": "【系统】",
}


def _role_prefix(role: str) -> str | None:
    if role in ROLE_PREFIX:
        return ROLE_PREFIX[role]
    if role.endswith("的动作"):
        return f"【{role}】"
    if role:
        # NPC and other actor ids: still deliver so the user is not left silent.
        return f"【{role}】"
    return None


def deliver_outputs(outputs: list[dict], delay: float = 3.0):
    """
    逐条发送 room tick 的输出。

    每个 output 是一条独立消息，原样发送。
    空内容跳过。间隔 delay 秒防止 iMessage 合并。
    """
    from langfuse_logger import LangfuseCtx, log_event

    env = os.environ.copy()
    env["PHOTON_SIDECAR_TOKEN"] = os.environ.get("PHOTON_SIDECAR_TOKEN", "")
    env["PHOTON_SIDECAR_PORT"] = "8789"
    env["PHOTON_SIDECAR_AUTOSTART"] = "false"

    sent = 0
    skipped = 0
    total = len(outputs)
    lf_ctx = LangfuseCtx(source="deliver")
    log_event(
        lf_ctx,
        "deliver.start",
        input_data={
            "count": total,
            "roles": [item.get("role") for item in outputs],
        },
    )

    for i, item in enumerate(outputs):
        role = item.get("role", "")
        text = item.get("text", "").strip()
        prefix = _role_prefix(role)
        if not text:
            skipped += 1
            log_event(
                lf_ctx,
                "deliver.skip_empty",
                input_data={"index": i, "role": role},
                level="WARNING",
            )
            continue
        if prefix is None:
            skipped += 1
            log_event(
                lf_ctx,
                "deliver.skip_role",
                input_data={"index": i, "role": role},
                level="WARNING",
            )
            continue

        payload = f"{prefix}{text}"
        try:
            r = subprocess.run(
                ["hermes", "send", "--to", "photon", payload],
                capture_output=True, text=True, timeout=30, env=env,
            )
            if r.returncode == 0:
                sent += 1
                log_event(
                    lf_ctx,
                    "deliver.sent",
                    input_data={
                        "index": i,
                        "role": role,
                        "chars": len(payload),
                    },
                    output_data=payload[:200],
                )
            else:
                print(f"[deliver] fail: {r.stderr.strip()[:80]}", file=sys.stderr)
                skipped += 1
                log_event(
                    lf_ctx,
                    "deliver.send_failed",
                    input_data={"index": i, "role": role},
                    output_data={
                        "returncode": r.returncode,
                        "stderr": (r.stderr or "")[:300],
                        "stdout": (r.stdout or "")[:300],
                    },
                    level="ERROR",
                )
        except Exception as e:
            print(f"[deliver] error: {e}", file=sys.stderr)
            skipped += 1
            log_event(
                lf_ctx,
                "deliver.send_exception",
                input_data={"index": i, "role": role},
                output_data={"error": str(e)},
                level="ERROR",
            )

        if i < total - 1:
            time.sleep(delay)

    log_event(
        lf_ctx,
        "deliver.done",
        output_data={"sent": sent, "skipped": skipped, "total": total},
        level="WARNING" if sent == 0 and total else "DEFAULT",
    )
    return sent, skipped
