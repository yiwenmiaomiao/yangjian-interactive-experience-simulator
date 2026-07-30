#!/usr/bin/env python3
"""
杨戬 Room → BlueBubbles 轮询服务
每5秒查一次 BlueBubbles 的新消息 → 调 room.tick → 逐条回复
"""
import os, sys, json, uuid, time, requests

PROFILE = os.path.expanduser("~/Documents/yangjian-room")
BB_URL = "http://127.0.0.1:1234"
BB_PASS = os.environ.get("BLUEBUBBLES_PASSWORD", "")
CHAT_GUID = os.environ.get("BLUEBUBBLES_CHAT_GUID", "")

# 加载环境变量
env_path = os.path.join(PROFILE, ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k] = v

os.chdir(PROFILE)
sys.path.insert(0, os.path.join(PROFILE, "room"))

def send_imessage(text):
    try:
        r = requests.post(f"{BB_URL}/api/v1/message/text?password={BB_PASS}", json={
            "chatGuid": CHAT_GUID,
            "tempGuid": f"room-{uuid.uuid4().hex[:8]}",
            "method": "private-api",
            "message": text,
        }, timeout=30)
        return r.status_code == 200
    except Exception as e:
        print(f"[发错] {e}", flush=True)
        return False

def get_latest_messages(limit=3):
    """获取 chat 的最新消息"""
    try:
        url = f"{BB_URL}/api/v1/chat/{CHAT_GUID.replace(';', '%3B').replace('@', '%40')}/message?password={BB_PASS}&limit={limit}"
        r = requests.get(url, timeout=10)
        data = r.json()
        return data.get("data", [])
    except Exception as e:
        print(f"[查错] {e}", flush=True)
        return []

def process_message(text):
    print(f"\n📩 收到: {text}", flush=True)
    try:
        import importlib
        spec = importlib.util.spec_from_file_location("room_mod", os.path.join(PROFILE, "room", "room.py"))
        room_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(room_mod)
        result = room_mod.tick(user_message=text, source="bluebubbles")
        
        if result.get("ok"):
            sent_count = 0
            for item in result.get("output", []):
                role = item.get("role", "")
                item_text = item.get("text", "").strip()
                if not item_text:
                    continue
                
                if role == "杨戬的动作":
                    msg = f"【杨戬的动作】{item_text}"
                elif role == "杨戬":
                    msg = f"【杨戬】{item_text}"
                elif role == "旁白":
                    msg = f"【旁白】{item_text}"
                elif role.endswith("的动作"):
                    msg = f"【{role}】{item_text}"
                else:
                    msg = f"【{role}】{item_text}"
                
                if send_imessage(msg):
                    sent_count += 1
                time.sleep(0.5)
            
            print(f"✅ 回复 {sent_count} 条", flush=True)
        else:
            print(f"❌ Tick失败", flush=True)
            send_imessage("【系统】杨戬暂时无法回应，稍后再试。")
    except Exception as e:
        print(f"❌ 处理失败: {e}", flush=True)
        import traceback; traceback.print_exc()

# ---- 主循环 ----
print("杨戬 Room 轮询服务启动", flush=True)
print(f"监听 chat: {CHAT_GUID}", flush=True)

# 记录启动时间，只处理之后的消息
start_time = int(time.time() * 1000)  # 毫秒
processed_guids = set()

print(f"启动时间戳: {start_time}", flush=True)

while True:
    try:
        msgs = get_latest_messages(10)
        
        for msg in reversed(msgs):
            guid = msg.get("guid", "")
            text = msg.get("text", "").strip()
            created = msg.get("dateCreated", 0)
            
            if not text or guid in processed_guids:
                continue
            if created < start_time:
                processed_guids.add(guid)
                continue
            
            # 跳过系统自己发的回复
            if text.startswith("【旁白】") or text.startswith("【杨戬") or text.startswith("【系统】"):
                processed_guids.add(guid)
                continue
            
            # 新消息！
            processed_guids.add(guid)
            print(f"📩 新消息 GUID={guid[:12]} text={text[:40]}", flush=True)
            process_message(text)
            break  # 一次只处理一条
        
        time.sleep(3)
    except KeyboardInterrupt:
        print("\n关闭...", flush=True)
        break
    except Exception as e:
        print(f"[循环错] {e}", flush=True)
        time.sleep(5)
