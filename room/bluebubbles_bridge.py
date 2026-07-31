#!/usr/bin/env python3
"""
杨戬 Room → BlueBubbles 双保险桥接
webhook（主）+ 轮询（兜底）
"""
import os, sys, json, uuid, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler

PROFILE = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))
BB_URL = "http://127.0.0.1:1234"
BB_PASS = os.environ.get("BLUEBUBBLES_PASSWORD", "")
CHAT_GUID = os.environ.get("BLUEBUBBLES_CHAT_GUID", "")
PORT = 8645

# 加载环境
env_path = os.path.join(PROFILE, ".env")
if os.path.exists(env_path):
    for line in open(env_path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k] = v

os.chdir(PROFILE)
sys.path.insert(0, os.path.join(PROFILE, "room"))

import importlib
spec = importlib.util.spec_from_file_location("room_mod", os.path.join(PROFILE, "room", "room.py"))
room_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(room_mod)

# 共享状态
processed_guids = set()     # 已处理的 GUID
processed_lock = threading.Lock()
start_time = int(time.time() * 1000)

def send_imessage(text):
    import requests
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

def process_text(text, guid=""):
    """处理一条消息文本"""
    global room_mod
    text = text.strip()
    if not text:
        return
    
    # 跳过系统自己的回复
    if text.startswith("【旁白】") or text.startswith("【杨戬") or text.startswith("【系统】"):
        return
    
    # 检测用户输入类型
    is_action = text.startswith("（") or text.startswith("【")
    user_prefix = "（行动）" if is_action else "（对话）"
    
    print(f"\n📩 {user_prefix} {text[:60]}", flush=True)
    try:
        result = room_mod.tick(user_message=text, source="bluebubbles")
        
        if result.get("ok"):
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
                
                send_imessage(msg)
                time.sleep(0.5)
            
            print(f"✅ 回复 {len(result.get('output',[]))} 条", flush=True)
        else:
            print(f"❌ Tick失败", flush=True)
            send_imessage("【系统】杨戬暂时无法回应。")
    except Exception as e:
        print(f"❌ {e}", flush=True)
        import traceback; traceback.print_exc()

# ─── webhook ───
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        
        try:
            payload = json.loads(raw)
        except:
            payload = {}
        
        data = payload.get("data", payload)
        msg = data.get("message", data)
        text = msg.get("text", "") if isinstance(msg, dict) else str(msg or "")
        guid = msg.get("guid", "") if isinstance(msg, dict) else ""
        
        with processed_lock:
            if guid in processed_guids or not text.strip():
                self._ok()
                return
            processed_guids.add(guid)
        
        process_text(text, guid)
        self._ok()
    
    def _ok(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Yangjian Room Bridge + Poll\n")
    
    def log_message(self, format, *args):
        pass

# ─── 轮询兜底 ───
def poll_loop():
    import requests
    url = f"{BB_URL}/api/v1/chat/{CHAT_GUID.replace(';','%3B').replace('@','%40')}/message?password={BB_PASS}&limit=5"
    
    while True:
        try:
            r = requests.get(url, timeout=10)
            for msg in r.json().get("data", []):
                guid = msg.get("guid", "")
                text = msg.get("text", "").strip()
                created = msg.get("dateCreated", 0)
                
                if not text or created < start_time:
                    continue
                
                with processed_lock:
                    if guid in processed_guids:
                        continue
                    processed_guids.add(guid)
                
                process_text(text, guid)
        except Exception as e:
            pass  # 静默失败，等下一轮
        
        time.sleep(10)

# ─── 启动 ───
if __name__ == "__main__":
    # 注册 webhook
    import requests
    try:
        old = requests.get(f"{BB_URL}/api/v1/webhook?password={BB_PASS}", timeout=10)
        for wh in old.json().get("data", []):
            wid = wh.get("id")
            if wid:
                requests.delete(f"{BB_URL}/api/v1/webhook/{wid}?password={BB_PASS}", timeout=10)
        
        wh_url = f"http://localhost:{PORT}/bluebubbles-webhook?password={BB_PASS}"
        requests.post(f"{BB_URL}/api/v1/webhook?password={BB_PASS}", json={
            "url": wh_url,
            "events": ["new-message", "updated-message"],
        }, timeout=10)
        print(f"Webhook 已注册", flush=True)
    except Exception as e:
        print(f"Webhook 注册失败: {e}", flush=True)
    
    # 启动轮询线程
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()
    
    server = HTTPServer(("127.0.0.1", PORT), WebhookHandler)
    print(f"🌙 杨戬 Room Bridge :{PORT} (webhook + 10s poll)", flush=True)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
