#!/usr/bin/env python3
"""
AppleScript iMessage 桥接 — 不依赖 BlueBubbles/Photon，直接调 Messages.app

发送：osascript → Messages.app
接收：轮询 ~/Library/Messages/chat.db (SQLite)
"""

import os
import sqlite3
import subprocess
import time
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── 配置 ──

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
POLL_INTERVAL = 3  # 秒
MY_ADDRESSES = []  # 你的 Apple ID/手机号，用于过滤自己的消息

# ── 发送 ──

def send_imessage(phone: str, text: str) -> bool:
    """
    通过 AppleScript 发送 iMessage。
    
    Args:
        phone: 收件人号码（如 "+86xxxxxxxxxxx"）
        text: 消息内容
    
    Returns:
        是否成功
    """
    # AppleScript 需要处理特殊字符
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{phone}" of targetService
        send "{escaped_text}" to targetBuddy
    end tell
    '''
    
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"[AppleScript] 发送失败: {result.stderr.strip()}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("[AppleScript] 发送超时")
        return False
    except Exception as e:
        print(f"[AppleScript] 发送异常: {e}")
        return False


def send_imessage_with_retry(phone: str, text: str, max_retries: int = 2) -> bool:
    """带重试的发送。"""
    for attempt in range(max_retries + 1):
        if send_imessage(phone, text):
            return True
        if attempt < max_retries:
            time.sleep(1)
    return False


# ── 接收（轮询 chat.db）──

class MessagePoller:
    """
    轮询 ~/Library/Messages/chat.db 获取新消息。
    
    工作原理：
    - chat.db 是 SQLite 数据库，Messages.app 写入
    - message表：id, text, handle_id, date, is_from_me, ...
    - handle表：id, id(即手机号/Apple ID)
    - chat表：chat_id, ...
    - chat_message_join：关联 chat 和 message
    """
    
    def __init__(self, target_phone: str):
        self.target_phone = target_phone
        self.last_message_id = self._get_latest_message_id()
        print(f"[MessagePoller] 从 message_id={self.last_message_id} 开始监听")
    
    def _get_latest_message_id(self) -> int:
        """获取当前最大的 message_id。"""
        try:
            conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
            cursor = conn.execute("SELECT MAX(ROWID) FROM message")
            result = cursor.fetchone()[0]
            conn.close()
            return result or 0
        except Exception as e:
            print(f"[MessagePoller] 读取 chat.db 失败: {e}")
            return 0
    
    def get_new_messages(self) -> list[dict]:
        """
        获取从上次检查以来的新消息。
        
        Returns:
            [{"id": int, "text": str, "sender": str, "timestamp": float}, ...]
        """
        if not CHAT_DB.exists():
            print(f"[MessagePoller] chat.db 不存在: {CHAT_DB}")
            return []
        
        try:
            # 使用 WAL 模式以支持并发读取
            conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
            conn.execute("PRAGMA query_only = ON")
            
            query = """
                SELECT 
                    m.ROWID as id,
                    m.text,
                    h.id as sender,
                    m.date / 1000000000 + 978307200 as timestamp,
                    m.is_from_me,
                    m.cache_roomnames
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.ROWID > ?
                  AND m.is_from_me = 0
                  AND m.text IS NOT NULL
                  AND m.text != ''
                ORDER BY m.ROWID ASC
            """
            
            cursor = conn.execute(query, (self.last_message_id,))
            rows = cursor.fetchall()
            conn.close()
            
            new_messages = []
            for row in rows:
                msg_id, text, sender, timestamp, is_from_me, room = row
                
                # 检查是否是发给目标号码的消息（通过 chat 表关联）
                # 简单过滤：只收发给你的
                if sender and self.target_phone and sender != self.target_phone:
                    # 可能是一条发给别人的消息，跳过
                    # 但如果是别人发来的，sender 会是对方的号码
                    pass
                
                new_messages.append({
                    "id": msg_id,
                    "text": text,
                    "sender": sender,
                    "timestamp": timestamp,
                    "room": room,
                })
                self.last_message_id = max(self.last_message_id, msg_id)
            
            return new_messages
            
        except Exception as e:
            print(f"[MessagePoller] 查询 chat.db 失败: {e}")
            return []
    
    def poll_once(self) -> list[dict]:
        """单次轮询，返回新消息。"""
        return self.get_new_messages()
    
    def listen_loop(self, callback, interval: int = POLL_INTERVAL):
        """
        持续监听循环。
        
        Args:
            callback: 收到消息时的回调函数 callback(message_dict)
            interval: 轮询间隔（秒）
        """
        print(f"[MessagePoller] 开始监听，间隔 {interval}s")
        while True:
            try:
                messages = self.get_new_messages()
                for msg in messages:
                    try:
                        callback(msg)
                    except Exception as e:
                        print(f"[MessagePoller] 回调异常: {e}")
                time.sleep(interval)
            except KeyboardInterrupt:
                print("\n[MessagePoller] 停止监听")
                break
            except Exception as e:
                print(f"[MessagePoller] 监听异常: {e}")
                time.sleep(interval)


# ── 工具函数 ──

def get_my_phone_number() -> Optional[str]:
    """尝试获取本机号码（通过 AppleScript 查 Messages 账户）。"""
    script = '''
    tell application "Messages"
        set myHandles to every handle of every service
        return myHandles as string
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except:
        pass
    return None


def ensure_messages_access():
    """
    检查 Messages.app 是否有数据库访问权限。
    首次运行需要授予完全磁盘访问权限。
    """
    if not CHAT_DB.exists():
        print(f"[警告] chat.db 不存在: {CHAT_DB}")
        print("请确认 Messages.app 已登录 iMessage 账号")
        return False
    
    try:
        # 尝试只读打开
        conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
        conn.execute("SELECT COUNT(*) FROM message")
        conn.close()
        return True
    except Exception as e:
        print(f"[警告] 无法读取 chat.db: {e}")
        print("请在 系统设置 → 隐私与安全性 → 完全磁盘访问权限 中")
        print("授予终端/Terminal.app 权限")
        return False


# ── 测试 ──

def test_send(phone: str, text: str = "你好，我是杨戬。测试消息。"):
    """测试发送消息。"""
    print(f"发送给 {phone}: {text}")
    success = send_imessage_with_retry(phone, text)
    print(f"{'✓ 发送成功' if success else '✗ 发送失败'}")
    return success


def test_receive(phone: str, count: int = 3):
    """测试接收最近的消息。"""
    poller = MessagePoller(phone)
    print(f"最近 {count} 条消息：")
    
    try:
        conn = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
        query = """
            SELECT m.text, h.id as sender, 
                   datetime(m.date / 1000000000 + 978307200, 'unixepoch') as time,
                   m.is_from_me
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.text IS NOT NULL AND m.text != ''
            ORDER BY m.ROWID DESC
            LIMIT ?
        """
        cursor = conn.execute(query, (count,))
        for row in cursor.fetchall():
            text, sender, time_str, is_from_me = row
            direction = "→" if is_from_me else "←"
            print(f"  [{direction}] {sender} ({time_str}): {text[:60]}")
        conn.close()
    except Exception as e:
        print(f"读取失败: {e}")


# ── CLI ──

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AppleScript iMessage 桥接")
    parser.add_argument("--send", nargs=2, metavar=("PHONE", "TEXT"), help="发送消息")
    parser.add_argument("--recent", type=int, nargs="?", const=5, help="查看最近 N 条消息")
    parser.add_argument("--check", action="store_true", help="检查权限和连接")
    parser.add_argument("--listen", metavar="PHONE", help="持续监听模式（手机号）")
    
    args = parser.parse_args()
    
    if args.check:
        ok = ensure_messages_access()
        if ok:
            phone = get_my_phone_number()
            print(f"✓ chat.db 可读")
            if phone:
                print(f"  Apple ID: {phone}")
        exit(0 if ok else 1)
    
    if args.send:
        phone, text = args.send
        success = test_send(phone, text)
        exit(0 if success else 1)
    
    if args.recent is not None:
        test_receive("", args.recent)
        exit(0)
    
    if args.listen:
        poller = MessagePoller(args.listen)
        
        def on_message(msg):
            sender = msg["sender"] or "unknown"
            text = msg["text"][:100].replace("\n", " ")
            print(f"\n← {sender}: {text}")
            # TODO: 调用 Room 处理消息
        
        poller.listen_loop(on_message)
        exit(0)
    
    parser.print_help()
