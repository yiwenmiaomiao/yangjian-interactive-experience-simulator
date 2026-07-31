#!/usr/bin/env python3
"""
iMessage 轮询桥接器 — 实时检测用户回复，自动走 Room 流程

工作方式：
1. 轮询 Mac Messages 数据库，检测是否有用户新消息
2. 有则通过 photon_room_bridge 处理
3. 处理结果通过 deliver.py 逐条发送
"""
import os, sys, time, json, subprocess
from datetime import datetime

ROOM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "room")
sys.path.insert(0, ROOM_DIR)

MSG_DB = os.path.expanduser("~/Library/Messages/chat.db")
APPLE_EPOCH = 978307200  # Apple epoch offset

# 用户号码（谁的消息需要处理）
TARGET_NUMBERS = [os.environ.get("PHOTON_ALLOWED_USERS", "")] if os.environ.get("PHOTON_ALLOWED_USERS") else []


def get_last_message_id() -> int:
    """读取上次处理的消息 ID。"""
    path = "/tmp/imessage_poll_state.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f).get("last_id", 0)
    return 0


def save_last_message_id(msg_id: int):
    with open("/tmp/imessage_poll_state.json", "w") as f:
        json.dump({"last_id": msg_id, "updated_at": datetime.now().isoformat()}, f)


def fetch_new_messages(last_id: int) -> list[dict]:
    """从 Messages 数据库获取新收到的消息。"""
    import sqlite3
    try:
        conn = sqlite3.connect(MSG_DB)
        cur = conn.cursor()
        # 只查 is_from_me=0（收到的消息），按 rowid 递增的未处理消息
        cur.execute("""
            SELECT m.rowid, m.text, c.chat_identifier
            FROM message m
            JOIN chat_message_join cmj ON m.rowid = cmj.message_id
            JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE m.is_from_me = 0
              AND m.rowid > ?
              AND m.text IS NOT NULL
              AND m.text != ''
            ORDER BY m.rowid ASC
            LIMIT 5
        """, (last_id,))
        rows = cur.fetchall()
        conn.close()

        results = []
        for rowid, text, chat_id in rows:
            # 检查是否来自目标号码
            if any(num in str(chat_id) for num in TARGET_NUMBERS):
                results.append({"id": rowid, "text": text.strip(), "chat": chat_id})
        return results
    except Exception as e:
        print(f"[poll] DB error: {e}", file=sys.stderr)
        return []


def process_message(text: str) -> bool:
    """处理一条消息，返回是否成功。"""
    import importlib
    pb = importlib.import_module("photon_room_bridge")

    print(f"[poll] 处理: {text[:60]}...", file=sys.stderr)
    result = pb.handle_and_deliver(text, delay=2.0)

    if not result.get("ok"):
        print(f"[poll] tick 失败: {result.get('error','')}", file=sys.stderr)
        return False

    delivery = result.get("delivery", {})
    sent = delivery.get("sent", 0)
    skipped = delivery.get("skipped", 0)
    if not result.get("output", []):
        print(f"[poll] 无输出", file=sys.stderr)
        return True

    print(f"[poll] 发送 {sent}, 跳过 {skipped}", file=sys.stderr)
    return sent > 0


def poll_loop(interval: float = 3.0):
    """主轮询循环。"""
    last_id = get_last_message_id()
    print(f"[poll] 启动, last_id={last_id}, interval={interval}s", file=sys.stderr)

    while True:
        try:
            messages = fetch_new_messages(last_id)
            for msg in messages:
                if msg["id"] <= last_id:
                    continue
                print(f"[poll] 新消息 #{msg['id']}: {msg['text'][:60]}", file=sys.stderr)
                ok = process_message(msg["text"])
                if ok:
                    last_id = msg["id"]
                    save_last_message_id(last_id)
                time.sleep(1)  # 消息间间隔
        except KeyboardInterrupt:
            print(f"\n[poll] 停止", file=sys.stderr)
            break
        except Exception as e:
            print(f"[poll] 异常: {e}", file=sys.stderr)

        time.sleep(interval)


if __name__ == "__main__":
    poll_loop(interval=3.0)
