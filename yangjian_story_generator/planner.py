"""
Main Arc Planner — 基于 Hermes 模型的 StructuredModelClient 实现

调用 DeepSeek 生成符合 StoryPlan 结构的私有故事计划。
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping

from .codec import story_plan_from_dict, story_plan_to_json, story_plan_public_summary
from .generator import StoryBrief, StoryGenerator, GeneratedPlanInvalidError
from .llm_bridge import call_llm
from .models import (
    CharacterContext,
    PreferenceSnapshot,
    StoryPlan,
    StoryStandard,
)
from .validation import StoryPlanValidator

PROJECT_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))


# ── 大纲风格加载 ──────────────────────────────────────────

_OUTLINE_STYLE_CACHE: str | None = None


def _load_outline_style() -> str:
    """从 skills/story-outline-style/SKILL.md 加载大纲风格，作为 system prompt。

    优先读环境变量 YANGJIAN_OUTLINE_STYLE_FILE 指定的文件，
    默认读项目目录下的 skills/story-outline-style/SKILL.md。
    解析时去掉 YAML frontmatter，只保留 markdown body。
    """
    global _OUTLINE_STYLE_CACHE
    if _OUTLINE_STYLE_CACHE is not None:
        return _OUTLINE_STYLE_CACHE

    import re

    style_file = os.environ.get("YANGJIAN_OUTLINE_STYLE_FILE") or os.path.join(
        PROJECT_DIR, "skills", "story-outline-style", "SKILL.md"
    )

    try:
        with open(style_file, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        # Fallback: minimal system prompt if skill file missing
        _OUTLINE_STYLE_CACHE = (
            "你是一个分支故事架构师。构建私有分支故事计划。\n"
            "主线围绕用户（小仙汉）与杨戬，NPC 只在副线。"
            "不要预写对白，只定义 beat 结构和意图。"
        )
        return _OUTLINE_STYLE_CACHE

    # Strip YAML frontmatter (--- ... ---)
    body = re.sub(r"^---\n.*?\n---\n?", "", raw, flags=re.DOTALL).strip()
    _OUTLINE_STYLE_CACHE = body
    return _OUTLINE_STYLE_CACHE


# ── 模型适配器（用 room 的 llm 模块） ────────────────────────


class HermesModelClient:
    """适配 Hermes 模型栈的 StructuredModelClient。"""

    def __init__(self, temperature: float = 0.7, max_tokens: int = 128000) -> None:
        self._temperature = temperature
        self._max_tokens = max_tokens

    def generate_story_plan(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """调用模型生成 StoryPlan 结构的 JSON。"""
        system_prompt = self._build_system_prompt(payload)
        user_prompt = self._build_user_prompt(payload)
        raw = call_llm(
            system=system_prompt,
            user=user_prompt,
            agent_id="story_generator.plan",
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return self._parse_json(raw)

    def _build_system_prompt(self, payload: Mapping[str, Any]) -> str:
        return _load_outline_style()

    def _build_user_prompt(self, payload: Mapping[str, Any]) -> str:
        rules = payload.get("rules", {})
        brief = payload.get("brief", {})
        character = payload.get("character_context", {})
        preferences = payload.get("preference_snapshot", {})
        standard = payload.get("story_standard", {})

        prompt = f"""## 生成请求
任务：生成一个私有分支故事计划

## 主题/梗概
{brief.get('premise_seed', '未指定')}
偏好主题：{brief.get('preferred_themes', [])}

## 规则
{json.dumps(rules, ensure_ascii=False, indent=2)}

## 标准
{json.dumps(standard, ensure_ascii=False, indent=2)}

## 杨戬角色理解
{json.dumps(character, ensure_ascii=False, indent=2)}

## 用户偏好快照
{json.dumps(preferences, ensure_ascii=False, indent=2)}

## 副线（side_arcs）结构规则
副线不通过 unlock 条件触发，而是通过主线 beat 的 transition 进入：
- 主线某 beat（如 m2）的某个 transition，其 target_id 指向副线首个 beat（如 s1_1）；当该 transition 的 goal 满足时，故事推进进入副线。
- 副线内部 beat 之间通过 transition 串联（如 s1_1 -> s1_2）。
- 副线完成后，最后一个 beat 的 transition 必须指向主线 beat，且必须是“进入副线前那个主线 beat 的下一个主线 beat”（例如从 m2 进入副线，则副线末 beat 指向 m3）。
- 副线之间不能直接跳转（一个副线的 beat 不能 transition 到另一个副线的 beat）。
- 副线不再拥有 endings 字段；副线通过 transition 回归主线。

## 输出 JSON 结构
你必须输出严格的 JSON，格式如下：

{{
  "story_id": "{brief.get('story_id', 'story_1')}",
  "created_at": "{brief.get('created_at', '')}",
  "premise": "故事前提",
  "theme": "核心主题",
  "main_arc": {{
    "goal": "主线目标描述",
    "beats": [
      {{
        "beat_id": "m1",
        "plot": "这个beat的剧情描述",
        "participants": ["user", "yangjian"],
        "allowed_information": [],
        "forbidden_information": [],
        "npc_requirement_ids": [],
        "diversion_allowed": false,
        "world_day": "第一天",
        "time_of_day": "清晨",
        "weather": "薄雾微凉",
        "location": "灌江口·杨府",
        "mood": "平静",
        "transitions": [
          {{
            "transition_id": "m1_to_m2",
            "target_id": "m2",
            "goal": "满足此方向推进的条件（如：用户发现了密室、用户选择与杨戬合作等）",
            "preserved_consequences": ["用户做了什么的重要事实"]
          }}
        ]
      }}
    ],
    "endings": [
      {{
        "ending_id": "main_end",
        "summary": "结局A描述",
      }}
    ]
  }},
  "side_arcs": [
    {{
      "arc_id": "side_1",
      "purpose": "副线目的",
      "impact_on_main_arc": ["对主线的影响"],
      "beats": [
        {{
          "beat_id": "s1_1",
          "plot": "副线首 beat 剧情",
          "participants": ["user", "yangjian"],
          "allowed_information": [],
          "forbidden_information": [],
          "npc_requirement_ids": ["npc_req_1"],
          "diversion_allowed": false,
          "world_day": "第一天",
          "time_of_day": "黄昏",
          "weather": "阴",
          "location": "灌江口·集市",
          "mood": "紧张",
          "transitions": [
            {{
              "transition_id": "s1_1_to_s1_2",
              "target_id": "s1_2",
              "goal": "副线推进条件",
              "preserved_consequences": ["副线关键事实"]
            }}
          ]
        }},
        {{
          "beat_id": "s1_2",
          "plot": "副线末 beat 剧情",
          "participants": ["user", "yangjian"],
          "allowed_information": [],
          "forbidden_information": [],
          "npc_requirement_ids": ["npc_req_1"],
          "diversion_allowed": false,
          "world_day": "第一天",
          "time_of_day": "入夜",
          "weather": "雨",
          "location": "灌江口·集市",
          "mood": "缓和",
          "transitions": [
            {{
              "transition_id": "s1_2_to_m3",
              "target_id": "m3",
              "goal": "副线结束、回归主线的条件",
              "preserved_consequences": ["副线对主线的影响事实"]
            }}
          ]
        }}
      ],
      "npc_requirements": [
        {{
          "requirement_id": "npc_req_1",
          "story_id": "story_1",
          "arc_id": "side_1",
          "narrative_function": "catalyst",
          "purpose": "NPC的目的",
          "npc_background": "背景",
          "relation_to_yangjian": "与杨戬的关系",
          "relation_to_user": "与用户的关系",
          "current_goal": "当前目标",
          "must_know": [],
          "must_not_know": ["main_ending"],
          "reusable": false,
          "constraints": []
        }}
      ]
    }}
  ],
  "npc_profiles": [
    {{
      "profile_id": "npc_profile_1",
      "requirement_id": "npc_req_1",
      "narrative_function": "catalyst",
      "name": "NPC姓名",
      "public_role": "公开身份",
      "personality": ["稳定人格特征"],
      "background": "完整但简洁的背景",
      "expression_style": "表达方式",
      "goals": ["角色目标"],
      "relation_to_yangjian": "与杨戬的初始关系",
      "relation_to_user": "与用户的初始关系",
      "knows": ["允许知道的事实ID"],
      "must_not_know": ["绝对不可知道的事实ID"],
      "behavior_boundaries": ["不可突破的行为边界"],
      "memory_seed": ["初始记忆"],
      "story_bindings": ["side_1", "s1_1"],
      "reusable": false,
      "profile_version": 1
    }}
  ],
  "secrets": [],
  "global_constraints": []
}}

不要输出 markdown 包裹，只输出纯 JSON。"""

        return prompt

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """从模型输出中提取并清理 JSON。"""
        text = raw.strip()
        for prefix in ("```json", "```"):
            if prefix in text:
                text = text.split(prefix, 1)[1]
                text = text.rsplit("```", 1)[0]
                break
        data = json.loads(text.strip())
        return _sanitize_plan(data)


def _sanitize_plan(data: dict[str, Any]) -> dict[str, Any]:
    """清理模型输出中的常见格式问题。"""
    # 清理 secrets
    if "secrets" in data:
        data["secrets"] = [
            s for s in data["secrets"]
            if isinstance(s, dict) and s.get("secret_id") and s.get("description")
        ]
    if "npc_profiles" in data:
        data["npc_profiles"] = [
            profile
            for profile in data["npc_profiles"]
            if (
                isinstance(profile, dict)
                and profile.get("profile_id")
                and profile.get("requirement_id")
                and profile.get("name")
                and profile.get("personality")
                and profile.get("goals")
            )
        ]
    # 清理 side_arcs
    if "side_arcs" in data:
        cleaned = []
        for arc in data["side_arcs"]:
            if not isinstance(arc, dict):
                continue
            # 清理 NPC requirements
            if "npc_requirements" in arc:
                arc["npc_requirements"] = [
                    n for n in arc["npc_requirements"]
                    if isinstance(n, dict) and n.get("requirement_id") and n.get("purpose")
                ]
            # 清理 beats
            if "beats" in arc:
                for beat in arc["beats"]:
                    if isinstance(beat, dict):
                        for field in ("transitions",):
                            if field in beat:
                                beat[field] = [
                                    t for t in beat[field]
                                    if isinstance(t, dict) and t.get("transition_id") and t.get("target_id")
                                ]
            cleaned.append(arc)
        data["side_arcs"] = cleaned
    # 清理 main_arc
    if "main_arc" in data and isinstance(data["main_arc"], dict):
        arc = data["main_arc"]
        if "beats" in arc:
            for beat in arc["beats"]:
                if isinstance(beat, dict):
                    for field in ("transitions",):
                        if field in beat:
                            beat[field] = [
                                t for t in beat[field]
                                if isinstance(t, dict) and t.get("transition_id") and t.get("target_id")
                            ]
    return data


# ── 高层 API ──────────────────────────────────────────────────


def generate_story_plan(
    *,
    character: CharacterContext,
    preferences: PreferenceSnapshot,
    brief: StoryBrief,
    standard: StoryStandard | None = None,
    temperature: float = 0.7,
    max_retries: int = 2,
) -> StoryPlan:
    """生成并校验故事计划。失败时自动重试。"""
    model = HermesModelClient(temperature=temperature)
    generator = StoryGenerator(model, standard=standard)

    last_error: Exception | None = None
    for attempt in range(1 + max_retries):
        try:
            return generator.generate(
                character=character,
                preferences=preferences,
                brief=brief,
            )
        except (GeneratedPlanInvalidError, ValueError, KeyError, TypeError) as e:
            last_error = e
            reason = ""
            if isinstance(e, GeneratedPlanInvalidError):
                reason = ", ".join(issue.code for issue in e.report.issues)
            else:
                reason = f"{type(e).__name__}: {e}"
            print(
                f"[story_generator] 生成失败 attempt={attempt + 1}/{max_retries + 1} "
                f"reason={reason[:200]}",
                flush=True,
            )
            if attempt < max_retries:
                continue
            raise
    assert last_error is not None
    raise last_error


async def generate_story_plan_async(
    *,
    character: CharacterContext,
    preferences: PreferenceSnapshot,
    brief: StoryBrief,
    standard: StoryStandard | None = None,
) -> StoryPlan:
    """异步版本（当前使用同步实现，后续可改为真异步）。"""
    return generate_story_plan(
        character=character,
        preferences=preferences,
        brief=brief,
        standard=standard,
    )


# ── 保存 / 加载 ──────────────────────────────────────────────


def save_story_plan(plan: StoryPlan, path: str | None = None) -> str:
    """保存私有故事计划到 JSON 文件。"""
    if path is None:
        path = os.path.join(PROJECT_DIR, "contexts", f"story_plan_{plan.story_id}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(story_plan_to_json(plan))
    return path


def load_story_plan(story_id: str) -> StoryPlan | None:
    """从 JSON 文件加载故事计划。"""
    path = os.path.join(PROJECT_DIR, "contexts", f"story_plan_{story_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return story_plan_from_json(f.read())


def plan_public_status(plan: StoryPlan) -> dict[str, Any]:
    """返回故事计划的非剧透状态信息。"""
    return story_plan_public_summary(plan)
