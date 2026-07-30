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
}


def deliver_outputs(outputs: list[dict], delay: float = 3.0):
    """
    逐条发送 room tick 的输出。
    
    每个 output 是一条独立消息，原样发送。
    空内容跳过。不认识的 role 跳过。
    间隔 delay 秒防止 iMessage 合并。
    """
    env = os.environ.copy()
    env["PHOTON_SIDECAR_TOKEN"] = os.environ.get("PHOTON_SIDECAR_TOKEN", "")
    env["PHOTON_SIDECAR_PORT"] = "8789"
    env["PHOTON_SIDECAR_AUTOSTART"] = "false"

    sent = 0
    skipped = 0
    total = len(outputs)

    for i, item in enumerate(outputs):
        role = item.get("role", "")
        text = item.get("text", "").strip()
        if not text:
            skipped += 1
            continue
        if role not in ROLE_PREFIX:
            skipped += 1
            continue

        payload = f"{ROLE_PREFIX[role]}{text}"

        try:
            r = subprocess.run(
                ["hermes", "send", "--to", "photon", payload],
                capture_output=True, text=True, timeout=30, env=env,
            )
            if r.returncode == 0:
                sent += 1
            else:
                print(f"[deliver] fail: {r.stderr.strip()[:80]}", file=sys.stderr)
                skipped += 1
        except Exception as e:
            print(f"[deliver] error: {e}", file=sys.stderr)
            skipped += 1

        if i < total - 1:
            time.sleep(delay)  # 关键：间隔防合并

    return sent, skipped
