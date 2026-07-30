"""
LLM 调用封装
所有 Agent 共用同一个 DeepSeek API 调用逻辑
优先从环境变量读取 API key，其次从 project .env 文件读取
"""
import os, json, requests, time

# 优先环境变量，其次从 project .env 读取
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not API_KEY:
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                API_KEY = line.split("=", 1)[1]
                break

API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"

# 代理设置
PROXIES = {}
http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
no_proxy = os.environ.get("no_proxy", "")
if http_proxy:
    PROXIES["http"] = http_proxy
if https_proxy:
    PROXIES["https"] = https_proxy


def call(system, messages, temperature=0.7, max_tokens=2000, agent_id: str = ""):
    """调用 LLM，返回文本响应。

    Args:
        agent_id: 用于 Langfuse 日志的 agent 名称
    """
    if not API_KEY:
        return "【错误：DEEPSEEK_API_KEY 未设置】"

    full_messages = [{"role": "system", "content": system}]
    full_messages.extend(messages)

    payload = {
        "model": MODEL,
        "messages": full_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    start = time.time()
    for attempt in range(3):
        try:
            resp = requests.post(
                API_URL,
                headers=headers,
                json=payload,
                proxies=PROXIES if PROXIES else None,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            result = data["choices"][0]["message"]["content"]
            duration_ms = (time.time() - start) * 1000
            _log_llm_call(agent_id, system, messages, result, duration_ms)
            return result
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return f"【LLM 调用失败: {e}】"


def _log_llm_call(agent_id: str, system: str, messages: list, result: str, duration_ms: float):
    if not agent_id:
        return
    try:
        from langfuse_logger import LangfuseCtx, log_generation
        ctx = LangfuseCtx()
        log_generation(ctx, agent_id, system, messages, result, duration_ms)
    except Exception:
        pass

