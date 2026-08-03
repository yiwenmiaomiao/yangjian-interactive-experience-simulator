"""
故事线选择器
提供 /story_* 命令的路由：
- /story_select  列出所有可用故事线
- /story_X       切换到指定故事线
- /story_reset   重置当前故事线到起始状态
"""
from __future__ import annotations

import os
import json
import glob
import re
from typing import Any
from langfuse_logger import LangfuseCtx, log_event

PROFILE_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))
CONTEXTS_DIR = os.path.join(PROFILE_DIR, "contexts")
STORY_CONFIG_PATH = os.path.join(CONTEXTS_DIR, "story_config.json")


# ── 扫描可用故事 ─────────────────────────────────────────

def scan_stories() -> list[dict[str, str]]:
    """扫描 contexts/story_plan_*.json，返回 [{story_id, theme}]"""
    result = []
    for path in sorted(glob.glob(os.path.join(CONTEXTS_DIR, "story_plan_*.json"))):
        fname = os.path.basename(path)
        m = re.match(r"story_plan_(.+)\.json", fname)
        if not m:
            continue
        sid = m.group(1)
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            result.append({
                "story_id": sid,
                "theme": raw.get("theme", "（加载失败）"),
            })
        except Exception:
            result.append({"story_id": sid, "theme": "（加载失败）"})
    return result


def load_config() -> dict[str, Any]:
    if os.path.exists(STORY_CONFIG_PATH):
        with open(STORY_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"current_story_id": "story_1", "available_stories": []}


def save_config(cfg: dict[str, Any]):
    with open(STORY_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── 命令处理 ─────────────────────────────────────────────

def handle_command(user_message: str) -> dict[str, Any] | None:
    """如果消息是 /story_* 命令，返回处理结果；否则返回 None 让 room.tick 继续处理。"""
    msg = str(user_message).strip()
    if not msg.startswith("/story"):
        return None

    # /story_select
    if msg == "/story_select":
        return _do_select()

    # /story_reset
    if msg == "/story_reset":
        return _do_reset()

    # /story_X (切换到指定 story)
    m = re.match(r"^/story_(.+)$", msg)
    if m:
        raw = m.group(1)
        # /story_2 -> story_id = "story_2"; /story_select -> "select" is caught above
        story_id = f"story_{raw}" if not raw.startswith("story_") else raw
        if raw in ("select", "reset"):
            return None
        return _do_switch(story_id)

    return None


def _do_select() -> dict[str, Any]:
    """列出所有可用故事线"""
    available = scan_stories()
    try:
        import story_state as ss
        current = ss.get_current_story_id()
    except Exception:
        current = "story_1"

    lines = ["【故事线列表】"]
    for item in available:
        sid = item["story_id"]
        marker = " ← 当前" if sid == current else ""
        lines.append(f"  /{sid}  {item['theme']}{marker}")

    if not available:
        lines.append("  （暂无可用故事线）")

    lf_event = {
        "type": "story_select",
        "current_story_id": current,
        "available_count": len(available),
    }
    try:
        _ctx = LangfuseCtx(source="story_selector", user_message=None)
        log_event(_ctx, "story.command", input_data=lf_event, level="DEFAULT")
    except Exception:
        pass
    return {
        "ok": True,
        "type": "story_select",
        "current_story_id": current,
        "available_stories": available,
        "output": [{"role": "系统", "text": "\n".join(lines)}],
    }


def _do_switch(story_id: str) -> dict[str, Any]:
    """切换到指定故事线"""
    try:
        import story_state as ss
    except Exception as exc:
        return {
            "ok": False,
            "type": "story_switch",
            "error": str(exc),
            "output": [{"role": "系统", "text": f"模块加载失败：{exc}"}],
        }

    available = scan_stories()
    available_ids = [s["story_id"] for s in available]

    if story_id not in available_ids:
        try:
            _ctx = LangfuseCtx(source="story_selector", user_message=None)
            log_event(_ctx, "story.command", input_data={
                "type": "story_switch", "story_id": story_id, "result": "not_found"
            }, level="WARNING")
        except Exception:
            pass
        return {
            "ok": False,
            "type": "story_switch",
            "error": "not_found",
            "output": [
                {
                    "role": "系统",
                    "text": (
                        f"当前暂不存在该故事线，为您返回上轮对话状态。"
                    )
                }
            ],
        }

    # 执行切换
    result = ss.switch_story(story_id)
    if not result.get("ok"):
        return {
            "ok": False,
            "type": "story_switch",
            "error": result.get("error", "unknown"),
            "output": [{"role": "系统", "text": f"切换失败：{result.get('error')}"}],
        }

    # 已在该 story 的情况
    if result.get("message"):
        state = result.get("state")
        beat = state.get("current_beat_id", "unknown") if state else "unknown"
        lines = [
            result["message"],
            f"当前进度：{beat}",
            "请继续您的故事。",
        ]
        try:
            _ctx = LangfuseCtx(source="story_selector", user_message=None)
            log_event(_ctx, "story.command", input_data={
                "type": "story_switch", "story_id": story_id, "result": "already_active"
            }, level="DEFAULT")
        except Exception:
            pass
        return {
            "ok": True,
            "type": "story_switch",
            "output": [{"role": "系统", "text": "\n".join(lines)}],
        }

    lines = [
        f"已切换到故事线：/{story_id}",
        f"当前进度：{result.get('state', {}).get('current_beat_id', 'unknown')}",
        "请继续您的故事。",
    ]
    try:
        _ctx = LangfuseCtx(source="story_selector", user_message=None)
        log_event(_ctx, "story.command", input_data={
            "type": "story_switch",
            "from": result.get("from"),
            "to": result.get("to"),
            "result": "success",
        }, level="DEFAULT")
    except Exception:
        pass
    return {
        "ok": True,
        "type": "story_switch",
        "from": result.get("from"),
        "to": result.get("to"),
        "output": [{"role": "系统", "text": "\n".join(lines)}],
    }


def _do_reset() -> dict[str, Any]:
    """重置当前故事线"""
    try:
        import story_state as ss
    except Exception as exc:
        return {
            "ok": False,
            "type": "story_reset",
            "error": str(exc),
            "output": [{"role": "系统", "text": f"模块加载失败：{exc}"}],
        }

    current = ss.get_current_story_id()
    ss.reset_current_story()
    new_state = ss.activate_plan()

    lines = [
        "已为您重置当前故事线。",
        f"当前故事线：/{current}",
        f"起始 beat：{new_state.get('current_beat_id', 'unknown')}",
        "请继续您的故事。",
    ]
    try:
        _ctx = LangfuseCtx(source="story_selector", user_message=None)
        log_event(_ctx, "story.command", input_data={
            "type": "story_reset",
            "story_id": current,
            "start_beat": new_state.get("current_beat_id", "unknown"),
            "result": "success",
        }, level="DEFAULT")
    except Exception:
        pass
    return {
        "ok": True,
        "type": "story_reset",
        "current_story_id": current,
        "output": [{"role": "系统", "text": "\n".join(lines)}],
    }
