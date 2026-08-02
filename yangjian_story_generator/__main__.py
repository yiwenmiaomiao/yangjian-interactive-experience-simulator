"""Command-line entry point for the Yang Jian story generator.

Run with::

    python -m yangjian_story_generator --story-id story_1 --premise "..."

Loads (or regenerates) the Yang Jian CharacterContext, builds a preference
snapshot, calls the LLM to produce a validated StoryPlan, and saves it to
``contexts/story_plan_<story_id>.json``.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from .character_loader import (
    generate_character_context,
    load_character_context,
    save_character_context,
)
from .generator import GeneratedPlanInvalidError, StoryBrief
from .planner import generate_story_plan, save_story_plan
from .preference_store import PreferenceStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yangjian_story_generator",
        description="生成符合规范的私有分支故事计划并保存为 JSON。",
    )
    parser.add_argument("--story-id", default="story_1", help="故事 ID（默认 story_1）")
    parser.add_argument("--premise", default="", help="故事前提种子（premise_seed）")
    parser.add_argument(
        "--themes",
        default="",
        help="偏好主题，逗号分隔（如 信任,成长）",
    )
    parser.add_argument(
        "--constraints",
        default="",
        help="额外约束，逗号分隔",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="输出 JSON 路径；默认 contexts/story_plan_<story_id>.json",
    )
    parser.add_argument(
        "--regenerate-character",
        action="store_true",
        help="重新生成 CharacterContext（默认用 contexts/character_context.json 缓存）",
    )
    parser.add_argument("--temperature", type=float, default=0.7, help="生成温度")
    parser.add_argument("--max-retries", type=int, default=2, help="校验失败重试次数")
    return parser


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _load_character(regenerate: bool):
    if regenerate:
        print("[story_generator] 重新生成 CharacterContext...", flush=True)
        ctx = generate_character_context()
        saved = save_character_context(ctx)
        print(f"[story_generator] CharacterContext 已保存: {saved}", flush=True)
        return ctx
    ctx = load_character_context()
    if ctx is None:
        print(
            "[story_generator] 未找到缓存的 character_context.json，重新生成...",
            flush=True,
        )
        ctx = generate_character_context()
        save_character_context(ctx)
    return ctx


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    character = _load_character(args.regenerate_character)
    preferences = PreferenceStore().export_snapshot()
    print(
        f"[story_generator] 输入就绪: character={character.character_id} "
        f"preference={preferences.user_id} v{preferences.profile_version}",
        flush=True,
    )

    brief = StoryBrief(
        story_id=args.story_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        premise_seed=args.premise,
        preferred_themes=_split_csv(args.themes),
        additional_constraints=_split_csv(args.constraints),
    )

    try:
        plan = generate_story_plan(
            character=character,
            preferences=preferences,
            brief=brief,
            temperature=args.temperature,
            max_retries=args.max_retries,
        )
    except GeneratedPlanInvalidError as e:
        codes = ", ".join(issue.code for issue in e.report.issues)
        print(f"[story_generator] 生成失败，校验未通过: {codes}", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError) as e:
        print(f"[story_generator] 生成失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    path = save_story_plan(plan, args.output)
    beats = len(plan.main_arc.beats)
    sides = len(plan.side_arcs)
    print(
        f"[story_generator] 完成: story_id={plan.story_id} "
        f"主线 beats={beats} 副线={sides} -> {path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
