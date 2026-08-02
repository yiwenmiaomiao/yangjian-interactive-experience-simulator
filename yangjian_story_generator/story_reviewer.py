"""LLM-powered story plan reviewer.

Reviews a generated StoryPlan for semantic and logical consistency that
deterministic validation cannot catch — geography coherence, narrative
causality, character behavior, information flow, and side-arc entry/exit.

Called automatically by StoryGenerator after deterministic validation passes.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .codec import story_plan_to_json
from .llm_bridge import call_llm
from .models import StoryPlan


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewIssue:
    severity: str  # "error" | "warning"
    category: str  # "geography" | "narrative" | "character" | "information" | "side_arc" | "world_state"
    beat_id: str
    message: str
    suggestion: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReviewReport:
    story_id: str
    issues: list[ReviewIssue] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0

    @property
    def errors(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ReviewIssue]:
        return [i for i in self.issues if i.severity == "warning"]


_REVIEWER_MODEL = "deepseek-chat"


def review_plan(plan: StoryPlan, *, model: str | None = None) -> ReviewReport:
    """Review a story plan using the LLM.

    Returns a ReviewReport with semantic/logical issues, or an empty report
    if the plan passes review.
    """
    plan_json = story_plan_to_json(plan)
    plan_text = json.dumps(plan_json, ensure_ascii=False, indent=2)

    system = _reviewer_system_prompt()
    user = _reviewer_user_prompt(plan_text)

    raw = call_llm(
        system=system,
        user=user,
        agent_id="story_generator.review",
        temperature=0.3,
        max_tokens=32768,
        model=model or _REVIEWER_MODEL,
        json_mode=True,
    )

    return _parse_review(raw, plan.story_id)


# ── Prompt builders ─────────────────────────────────────────────────────────

_REVIEW_CATEGORIES = [
    ("geography", "地理一致性：transition goal 描述的行动方向是否与当前 beat 场景逻辑衔接，不出现地理跳跃（如从山中直接跳到镇外而不经过过渡）"),
    ("narrative", "叙事因果：每个 beat 的事件是否自然导致 transition goal 描述的选择，不出现逻辑断层"),
    ("character", "角色行为：NPC 和杨戬的行为是否与其人设、动机和关系状态一致"),
    ("information", "信息流：allowed_information / forbidden_information 是否在 beat 之间造成矛盾或信息泄露"),
    ("side_arc", "副线连贯：side arc 的入口（从哪个主线 beat 进入）和出口（回到哪个主线 beat）是否与主线情节逻辑衔接，副线内部的 beat 是否地理连贯"),
    ("world_state", "世界状态：同一 world_day / time_of_day / weather / location 字段是否在相邻 beat 间保持一致或合理过渡"),
]


def _reviewer_system_prompt() -> str:
    categories_md = "\n".join(
        f"- **{k}**: {desc}" for k, desc in _REVIEW_CATEGORIES
    )
    return f"""你是一个故事架构审查员。你的任务是对一个私有分支故事计划进行严格的语义和逻辑审查。

审查范围（只报告你确认存在的问题）：
{ categories_md }

审查原则：
- 只报告你**确信**存在问题的项，不确定时选择不报告。
- 地理类问题：重点检查 transition goal 描述的方向是否与当前 beat plot 的场景吻合（如 beat 在"山中旧庙"，transition goal 写"去镇外"就是地理跳跃）。
- 叙事类问题：检查 beat 事件是否自然导出 transition goal 描述的选择。
- 副线问题：检查 side arc 的起点和终点是否与主线逻辑衔接（如从 m2 进入 side arc，side arc 结尾必须回到 m3 或之后，不能回到 m1）。
- 每个 beat 的 plot 必须以**场景/位置**开头，不能以内心活动或对话开头。

输出格式：严格 JSON，结构如下（不要有额外字段）：
{{
  "review_result": "pass" | "fail",
  "issues": [
    {{
      "severity": "error" | "warning",
      "category": "geography" | "narrative" | "character" | "information" | "side_arc" | "world_state",
      "beat_id": "具体出问题的 beat_id（如 m2、s1_1）",
      "message": "问题的简要描述",
      "suggestion": "修复建议"
    }}
  ]
}}

如果 review_result = "pass"，issues 数组为空数组 []。
"""


def _reviewer_user_prompt(plan_text: str) -> str:
    return f"""请审查以下故事计划 JSON，输出审查结果：

```json
{plan_text}
```
"""


# ── Output parser ────────────────────────────────────────────────────────────

def _parse_review(raw: str, story_id: str) -> ReviewReport:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ReviewReport(
            story_id=story_id,
            issues=[ReviewIssue(
                severity="warning",
                category="parse",
                beat_id="",
                message=f"Reviewer returned invalid JSON: {raw[:200]}",
                suggestion="Check model output format",
            )],
        )

    result = data.get("review_result", "pass")
    if result == "pass":
        return ReviewReport(story_id=story_id, issues=[])

    issues: list[ReviewIssue] = []
    for item in data.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issues.append(ReviewIssue(
            severity=item.get("severity", "warning"),
            category=item.get("category", "narrative"),
            beat_id=item.get("beat_id", ""),
            message=item.get("message", ""),
            suggestion=item.get("suggestion", ""),
        ))

    return ReviewReport(story_id=story_id, issues=issues)
