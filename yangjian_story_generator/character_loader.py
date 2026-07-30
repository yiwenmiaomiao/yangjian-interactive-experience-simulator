"""
CanonicalSourceLoader — 杨戬角色理解模块

读职杨戬真实历史故事和 SOUL.md，通过模型生成 CharacterContext。
禁止使用性格标签，必须基于具体经历、关系、选择和行为证据。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from .models import CanonicalEvent, CharacterContext, PreferenceSnapshot


def _resolve(rel: str) -> str:
    """Resolve a relative path under the b profile directory."""
    return os.path.join(os.path.expanduser("/Users/xiaoxianhan/Documents/yangjian-room"), rel)


# ── 原始资料加载 ─────────────────────────────────────────────


def list_canonical_stories() -> list[dict[str, str]]:
    """列出所有可用故事资料文件（不含 README）。"""
    stories_dir = _resolve("stories")
    result: list[dict[str, str]] = []
    for fname in sorted(os.listdir(stories_dir)):
        if not fname.endswith(".md") or fname == "README.md":
            continue
        path = os.path.join(stories_dir, fname)
        with open(path, encoding="utf-8") as f:
            content = f.read()
        result.append({"name": fname.replace(".md", ""), "content": content})
    return result


def load_soul() -> str:
    """加载杨戬 SOUL.md。"""
    path = _resolve("SOUL.md")
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_memory() -> str:
    """加载杨戬 MEMORY.md（如有）。"""
    path = _resolve("memories/MEMORY.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return ""


# ── 模型调用（复用 room 的 llm.py） ────────────────────────────


def _call_llm(system: str, user: str, temperature: float = 0.3, max_tokens: int = 4000) -> str:
    """调用 room 的 llm 模块。"""
    import sys
    sys.path.insert(0, _resolve("room"))
    import llm as room_llm
    return room_llm.call(
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ── CharacterContext 生成 ─────────────────────────────────────


def _build_analysis_prompt(soul: str, stories: list[dict[str, str]], memory: str) -> str:
    """构建模型分析的 prompt，要求基于证据输出 JSON，不使用性格标签。"""
    stories_text = "\n\n=======\n\n".join(
        f"【{s['name']}】\n{s['content'][:6000]}" for s in stories
    )

    return f"""你是杨戬行为分析专家。你的任务是基于杨戬的真实故事、SOUL 和记忆分析他，生成一份结构化角色理解档案。

## 核心规则
1. 禁止使用"冷静、高傲、温柔、责任感强"这类性格标签作为分析结论
2. 每个结论必须基于具体经历、事件或言行证据
3. 必须保留来源（哪个故事、哪个阶段）
4. 区分"他做了什么"和"为什么这样做"
5. 分析他在不同关系、不同情境下的不同表现
6. 指出哪些是他的核心立场（不易改变），哪些是开放解读（可自由发挥）

## 杨戬 SOUL
{soul}

## 杨戬记忆
{memory[:3000] if memory else "（无）"}

## 杨戬故事资料
{stories_text[:15000]}

## 输出格式
你必须输出以下 JSON 结构，不要包含其他文字：

{{
  "source_version": "canonical-sources-v1",

  "canonical_history": [
    {{
      "event_id": "事件唯一ID",
      "summary": "事件简述",
      "source_reference": "来源文件:阶段/章节",
      "lasting_effects": ["对该杨戬产生的长期影响1", "影响2"]
    }}
  ],

  "formative_experiences": [
    "对他产生长期塑造作用的经历描述（含来源）"
  ],

  "relationship_history": [
    "他与重要人物之间的关系演变（含来源证据）"
  ],

  "recurring_conflicts": [
    "反复出现的内部或外部冲突，如责任与个人感情的矛盾"
  ],

  "worldview": [
    "他如何理解责任、秩序、亲情、承诺和牺牲（基于具体言行）"
  ],

  "emotional_logic": [
    "影响他表达和处理感情方式的具体经历（不是性格描述，是因果逻辑）"
  ],

  "narrative_constraints": [
    "不能违背的历史事实清单",
    "不应被轻易改写的核心立场"
  ],

  "open_interpretations": [
    "原故事没有明确说明、允许角色自然发挥的部分"
  ]
}}

注意：formative_experiences 和 worldview 中的每一条都必须包含来源引用。
"""


def generate_character_context() -> CharacterContext:
    """从真实故事资料生成 CharacterContext。"""
    soul = load_soul()
    stories = list_canonical_stories()
    memory = load_memory()

    prompt = _build_analysis_prompt(soul, stories, memory)

    system = """你是一个文学角色分析专家。你的特点是：
1. 基于证据，不用标签
2. 关注行为模式、关系和情境差异
3. 区分核心设定和可发挥空间
4. 输出严格的结构化 JSON"""

    raw = _call_llm(system=system, user=prompt, temperature=0.3, max_tokens=4000)

    # 从模型输出中提取 JSON
    data = _extract_json(raw)

    events = tuple(
        CanonicalEvent(
            event_id=e["event_id"],
            summary=e["summary"],
            source_reference=e.get("source_reference", ""),
            lasting_effects=tuple(e.get("lasting_effects", [])),
        )
        for e in data.get("canonical_history", [])
    )

    return CharacterContext(
        character_id="yangjian",
        source_version=data.get("source_version", "canonical-sources-v1"),
        canonical_history=events,
        formative_experiences=tuple(data.get("formative_experiences", [])),
        relationship_history=tuple(data.get("relationship_history", [])),
        recurring_conflicts=tuple(data.get("recurring_conflicts", [])),
        worldview=tuple(data.get("worldview", [])),
        emotional_logic=tuple(data.get("emotional_logic", [])),
        narrative_constraints=tuple(data.get("narrative_constraints", [])),
        open_interpretations=tuple(data.get("open_interpretations", [])),
    )


# ── 工具函数 ──────────────────────────────────────────────────


def _extract_json(raw: str) -> dict[str, Any]:
    """从模型输出中提取 JSON（处理可能被 markdown 包裹的情况）。"""
    # 尝试直接解析
    for prefix in ("```json", "```"):
        if prefix in raw:
            raw = raw.split(prefix, 1)[1]
            raw = raw.rsplit("```", 1)[0]
            break
    raw = raw.strip()
    return json.loads(raw)


def export_context_to_dict(ctx: CharacterContext) -> dict[str, Any]:
    """导出 CharacterContext 为字典（用于保存/传递）。"""
    return asdict(ctx)


def save_character_context(ctx: CharacterContext, path: str | None = None) -> str:
    """保存 CharacterContext 到 JSON 文件。"""
    if path is None:
        path = _resolve("contexts/character_context.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(export_context_to_dict(ctx), f, ensure_ascii=False, indent=2)
    return path


def load_character_context(path: str | None = None) -> CharacterContext | None:
    """从 JSON 文件加载 CharacterContext。"""
    if path is None:
        path = _resolve("contexts/character_context.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return CharacterContext(
        character_id=data["character_id"],
        source_version=data["source_version"],
        canonical_history=tuple(
            CanonicalEvent(**e) for e in data.get("canonical_history", [])
        ),
        formative_experiences=tuple(data.get("formative_experiences", [])),
        relationship_history=tuple(data.get("relationship_history", [])),
        recurring_conflicts=tuple(data.get("recurring_conflicts", [])),
        worldview=tuple(data.get("worldview", [])),
        emotional_logic=tuple(data.get("emotional_logic", [])),
        narrative_constraints=tuple(data.get("narrative_constraints", [])),
        open_interpretations=tuple(data.get("open_interpretations", [])),
    )
