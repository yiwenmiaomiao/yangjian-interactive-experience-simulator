"""
Main Arc Planner — 基于 Hermes 模型的 StructuredModelClient 实现

调用 DeepSeek 生成符合 StoryPlan 结构的私有故事计划。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from typing import Any, Mapping

from .codec import story_plan_from_dict, story_plan_to_json, story_plan_public_summary
from .generator import StoryBrief, StoryGenerator, GeneratedPlanInvalidError
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


# ── 模型适配器（用 room 的 llm 模块） ────────────────────────


def _call_llm(system: str, user: str, temperature: float = 0.7, max_tokens: int = 8000) -> str:
    """调用 room 的 llm 模块生成故事计划。"""
    sys.path.insert(0, os.path.join(PROJECT_DIR, "room"))
    import llm as room_llm
    return room_llm.call(
        system=system,
        messages=[{"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )


class HermesModelClient:
    """适配 Hermes 模型栈的 StructuredModelClient。"""

    def __init__(self, temperature: float = 0.7, max_tokens: int = 8000) -> None:
        self._temperature = temperature
        self._max_tokens = max_tokens

    def generate_story_plan(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """调用模型生成 StoryPlan 结构的 JSON。"""
        system_prompt = self._build_system_prompt(payload)
        user_prompt = self._build_user_prompt(payload)
        raw = _call_llm(
            system=system_prompt,
            user=user_prompt,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return self._parse_json(raw)

    def _build_system_prompt(self, payload: Mapping[str, Any]) -> str:
        return """你是一个分支故事架构师。你的职责是构建私有分支故事计划。

## 核心约束
1. 主线唯一核心：用户（小仙汉）与杨戬
2. NPC 只能出现在副线，不能取代杨戬成为主线核心
3. 主线结局最多两个
4. 副线结局数量可配置
5. 用户的选择通过行为自然表达，不依赖菜单选项
6. 不要预写对白
7. 用户不能看到的内部结构（分支图、结局、旗标、伏笔含义）不得泄露

## 故事图要求
- 每个 Beat 必须有 transition 指向下一节点或结局
- 所有节点必须可从起始 Beat 到达
- 所有 Beat 必须有一条路径到达某个结局
- 不允许循环（除非明确配置）
- 分支收敛时必须保留用户选择产生的事实差异

## 节拍命名规范
- 主线： m1, m2, m3, ...
- 副线： s<数字>_1, s<数字>_2, ...（如 s1_1, s1_2）
- 结局： main_end, main_end_b, side_<id>_end_1, side_<id>_end_2"""

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

## 输出 JSON 结构
你必须输出严格的 JSON，格式如下：

{{
  "story_id": "{brief.get('story_id', 'story_1')}",
  "schema_version": 1,
  "created_at": "{brief.get('created_at', '')}",
  "character_snapshot_version": "{character.get('source_version', 'v1')}",
  "preference_snapshot_version": {preferences.get('profile_version', 1)},
  "story_standard_version": 1,
  "premise": "故事前提",
  "theme": "核心主题",
  "main_arc": {{
    "arc_id": "main",
    "goal": "主线目标描述",
    "start_beat_id": "m1",
    "milestones": ["信任建立", "秘密揭露", ...],
    "beats": [
      {{
        "beat_id": "m1",
        "purpose": "这个beat的目的",
        "participants": ["user", "yangjian"],
        "prerequisites": [],
        "allowed_information": [],
        "forbidden_reveals": [],
        "npc_requirement_ids": [],
        "reconverges_at": null,
        "transitions": [
          {{
            "transition_id": "m1_to_m2",
            "target_id": "m2",
            "conditions": [],
            "preserved_consequences": ["用户做了什么的重要事实"]
          }}
        ]
      }}
    ],
    "endings": [
      {{
        "ending_id": "main_end",
        "summary": "结局A描述",
        "conditions": []
      }}
    ]
  }},
  "side_arcs": [
    {{
      "arc_id": "side_1",
      "purpose": "副线目的",
      "impact_on_main_arc": ["对主线的影响"],
      "unlock": {{
        "minimum_main_progress": 0.3,
        "required_milestones": ["milestone_name"],
        "required_flags": []
      }},
      "start_beat_id": "s1_1",
      "beats": [...],
      "endings": [...],
      "npc_requirements": [
        {{
          "requirement_id": "npc_req_1",
          "story_id": "story_1",
          "side_arc_id": "side_1",
          "narrative_function": "catalyst",
          "purpose": "NPC的目的",
          "background_requirement": "背景",
          "relation_to_yangjian": "与杨戬的关系",
          "relation_to_user": "与用户的关系",
          "current_goal": "当前目标",
          "must_know": [],
          "must_not_know": ["main_ending"],
          "entry_condition": "",
          "exit_condition": "",
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
  "foreshadowing": [],
  "global_constraints": [],
  "forbidden_reveals": ["main_ending"]
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
    # 清理 foreshadowing——移除缺失字段的条目
    if "foreshadowing" in data:
        data["foreshadowing"] = [
            f for f in data["foreshadowing"]
            if isinstance(f, dict) and f.get("foreshadowing_id") and f.get("setup_beat_id") and f.get("payoff_beat_id")
        ]
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

    for attempt in range(1 + max_retries):
        try:
            return generator.generate(
                character=character,
                preferences=preferences,
                brief=brief,
            )
        except GeneratedPlanInvalidError as e:
            if attempt < max_retries:
                # 可以在此记录失败原因 e.report
                continue
            raise


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
