"""
LLM 调用封装
所有 Agent 共用同一个 DeepSeek API 调用逻辑
优先从环境变量读取 API key，其次从 project .env 文件读取
"""
import os, json, requests, time
from contextvars import ContextVar

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
_TRACE_CONTEXT = ContextVar("yangjian_llm_trace_context", default=None)

# DeepSeek deepseek-v4-flash rejects response_format.type=json_schema.
# Convert to json_object; callers still validate with Pydantic.
_JSON_SCHEMA_UNSUPPORTED = True

# 代理设置
PROXIES = {}
http_proxy = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
https_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
no_proxy = os.environ.get("no_proxy", "")
if http_proxy:
    PROXIES["http"] = http_proxy
if https_proxy:
    PROXIES["https"] = https_proxy


def normalize_response_format(response_format):
    """Map provider-unsupported formats onto what DeepSeek accepts.

    ``json_schema`` → ``json_object`` (schema enforcement stays in Pydantic).
    """
    if not response_format or not isinstance(response_format, dict):
        return response_format
    fmt_type = response_format.get("type")
    if fmt_type == "json_schema" and _JSON_SCHEMA_UNSUPPORTED:
        return {"type": "json_object"}
    if fmt_type == "json_object":
        return {"type": "json_object"}
    return response_format


def _is_unsupported_response_format_error(exc: BaseException, body: str = "") -> bool:
    text = f"{exc} {body}".lower()
    return (
        "response_format" in text
        and (
            "unavailable" in text
            or "json_schema" in text
            or "not support" in text
            or "unsupported" in text
        )
    )


def call(
    system,
    messages,
    temperature=0.7,
    max_tokens=2000,
    agent_id: str = "",
    response_format=None,
):
    """调用 LLM，返回文本响应。

    Args:
        agent_id: 用于 Langfuse 日志的 agent 名称
        response_format: 可选 JSON 模式。``json_schema`` is auto-converted
            to ``json_object`` for DeepSeek compatibility.
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
    normalized = normalize_response_format(response_format)
    if normalized is not None:
        payload["response_format"] = normalized

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
            if resp.status_code == 400:
                body = resp.text or ""
                # Late conversion if provider rejects a format we still sent.
                current = payload.get("response_format") or {}
                if (
                    _is_unsupported_response_format_error(
                        RuntimeError(body), body
                    )
                    and current.get("type") != "json_object"
                ):
                    payload["response_format"] = {"type": "json_object"}
                    continue
                resp.raise_for_status()
            resp.raise_for_status()
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            result = message.get("content")
            if result is None:
                result = ""
            duration_ms = (time.time() - start) * 1000
            meta = {
                "finish_reason": choice.get("finish_reason"),
                "usage": data.get("usage"),
                "model": data.get("model") or MODEL,
                "attempt": attempt + 1,
                "content_len": len(result) if isinstance(result, str) else 0,
                "refusal": message.get("refusal"),
            }
            if not str(result).strip():
                # Keep a compact raw choice for empty-content diagnosis.
                meta["empty_content"] = True
                meta["raw_choice"] = choice
                print(
                    f"[llm] empty content agent={agent_id or '-'} "
                    f"finish_reason={meta.get('finish_reason')} "
                    f"usage={meta.get('usage')} "
                    f"refusal={meta.get('refusal')!r} "
                    f"choice={json.dumps(choice, ensure_ascii=False)[:500]}",
                    flush=True,
                )
            _log_llm_call(
                agent_id,
                system,
                messages,
                result,
                duration_ms,
                metadata=meta,
            )
            return result
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                return f"【LLM 调用失败: {e}】"


def set_trace_context(context):
    _TRACE_CONTEXT.set(context)


def clear_trace_context():
    _TRACE_CONTEXT.set(None)


def _log_llm_call(
    agent_id: str,
    system: str,
    messages: list,
    result: str,
    duration_ms: float,
    metadata: dict | None = None,
):
    if not agent_id:
        return
    try:
        from langfuse_logger import LangfuseCtx, log_generation
        ctx = _TRACE_CONTEXT.get() or LangfuseCtx()
        log_generation(
            ctx,
            agent_id,
            system,
            messages,
            result,
            duration_ms,
            metadata=metadata,
        )
    except Exception:
        pass
