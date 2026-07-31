"""
故事运行状态管理器

保存完整故事运行状态，包括：
- 主线进度（milestone、branch）
- 已完成节点和已访问分支
- 旗标、关系状态、已揭露信息
- 活跃 NPC
- 待定选择
- 多用户/线程隔离
- 重启恢复
- 原子化保存
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# ── 运行状态数据模型 ────────────────────────────────────


@dataclass
class StoryRunState:
    story_id: str = ""
    user_id: str = "default"
    thread_id: str = "default"

    current_main_milestone: str = ""
    current_main_branch: str = ""

    active_side_arcs: list[str] = field(default_factory=list)
    completed_beats: list[str] = field(default_factory=list)
    visited_branches: list[str] = field(default_factory=list)

    flags: dict[str, Any] = field(default_factory=dict)
    relationship_state: dict[str, Any] = field(default_factory=dict)
    revealed_secrets: list[str] = field(default_factory=list)
    active_npcs: list[dict[str, Any]] = field(default_factory=list)

    pending_choice: dict[str, Any] = field(default_factory=lambda: {
        "candidates": [], "confidence": 0.0,
    })

    story_version: int = 1
    updated_at: str = ""


# ── 持久化 ──────────────────────────────────────────────


BASE_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))
STORY_RUN_DIR = os.path.join(BASE_DIR, "story_runs")


def _run_path(user_id: str, thread_id: str) -> str:
    os.makedirs(STORY_RUN_DIR, exist_ok=True)
    return os.path.join(STORY_RUN_DIR, f"run_{user_id}_{thread_id}.json")


def _atomic_write(path: str, data: dict[str, Any]) -> None:
    """原子化写入：写入临时文件后重命名。"""
    tmp = path + ".tmp." + str(int(time.time() * 1000000))
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)


def save(run: StoryRunState) -> None:
    """保存运行状态（原子化）。"""
    run.updated_at = datetime.now().isoformat()
    path = _run_path(run.user_id, run.thread_id)
    _atomic_write(path, asdict(run))


def load(user_id: str = "default", thread_id: str = "default") -> StoryRunState:
    """加载运行状态，不存在时返回默认状态。"""
    path = _run_path(user_id, thread_id)
    if not os.path.exists(path):
        run = StoryRunState(user_id=user_id, thread_id=thread_id)
        save(run)
        return run
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return StoryRunState(**data)
    except (json.JSONDecodeError, TypeError, KeyError):
        run = StoryRunState(user_id=user_id, thread_id=thread_id)
        save(run)
        return run


def delete(user_id: str, thread_id: str) -> None:
    """删除运行状态。"""
    path = _run_path(user_id, thread_id)
    if os.path.exists(path):
        os.remove(path)


# ── 高级操作 ────────────────────────────────────────────


def start_story(story_id: str, user_id: str = "default", thread_id: str = "default") -> StoryRunState:
    """开始一个故事。"""
    run = StoryRunState(
        story_id=story_id,
        user_id=user_id,
        thread_id=thread_id,
        story_version=1,
        updated_at=datetime.now().isoformat(),
    )
    # 同步激活底层的 story_state
    _sync_story_state(run)
    save(run)
    return run


def _sync_story_state(run: StoryRunState) -> None:
    """同步 run state 到低层的 story_state（beat 跟踪用）。"""
    import story_state as ss
    ss.load_plan(f"contexts/story_plan_{run.story_id}.json") if run.story_id else None
    state = ss.load_state()
    if state.get("status") != "active" and run.story_id:
        ss.reset_state()
        ss.activate_plan()


def record_beat_completed(run: StoryRunState, beat_id: str) -> StoryRunState:
    """记录一个 beat 完成。"""
    if beat_id not in run.completed_beats:
        run.completed_beats.append(beat_id)
        run.story_version += 1
    return run


def record_branch_visited(run: StoryRunState, branch_id: str) -> StoryRunState:
    """记录一个分支被访问。"""
    if branch_id not in run.visited_branches:
        run.visited_branches.append(branch_id)
        run.story_version += 1
    return run


def set_milestone(run: StoryRunState, milestone_id: str) -> StoryRunState:
    """推进主线里程碑。"""
    run.current_main_milestone = milestone_id
    run.story_version += 1
    return run


def reveal_secret(run: StoryRunState, secret_id: str) -> StoryRunState:
    """记录一个秘密被揭露。"""
    if secret_id not in run.revealed_secrets:
        run.revealed_secrets.append(secret_id)
        run.story_version += 1
    return run


def enter_side_arc(run: StoryRunState, arc_id: str) -> StoryRunState:
    """进入副线。"""
    if arc_id not in run.active_side_arcs:
        run.active_side_arcs.append(arc_id)
        run.story_version += 1
    return run


def exit_side_arc(run: StoryRunState, arc_id: str) -> StoryRunState:
    """退出副线。"""
    if arc_id in run.active_side_arcs:
        run.active_side_arcs.remove(arc_id)
        run.story_version += 1
    return run


def set_relationship(run: StoryRunState, subject: str, value: Any) -> StoryRunState:
    """设置关系状态（如 trust, affinity）。"""
    run.relationship_state[subject] = value
    run.story_version += 1
    return run


def set_flag(run: StoryRunState, key: str, value: Any = True) -> StoryRunState:
    """设置旗标。"""
    run.flags[key] = value
    run.story_version += 1
    return run


def set_pending_choice(run: StoryRunState, candidates: list[dict], confidence: float = 0.5) -> StoryRunState:
    """设置待定选择（低置信度分支时使用）。"""
    run.pending_choice = {
        "candidates": candidates,
        "confidence": confidence,
    }
    run.story_version += 1
    return run


def resolve_pending_choice(run: StoryRunState) -> StoryRunState:
    """清除待定选择。"""
    run.pending_choice = {"candidates": [], "confidence": 0.0}
    run.story_version += 1
    return run


def register_npc(run: StoryRunState, npc_id: str, arc_id: str, role: str = "") -> StoryRunState:
    """注册一个活跃 NPC。"""
    existing = [n for n in run.active_npcs if n.get("npc_id") == npc_id]
    if not existing:
        run.active_npcs.append({
            "npc_id": npc_id,
            "arc_id": arc_id,
            "role": role,
            "activated_at": datetime.now().isoformat(),
        })
        run.story_version += 1
    return run


def unregister_npc(run: StoryRunState, npc_id: str) -> StoryRunState:
    """注销 NPC。"""
    run.active_npcs = [n for n in run.active_npcs if n.get("npc_id") != npc_id]
    run.story_version += 1
    return run


# ── 快照与恢复 ──────────────────────────────────────────


def snapshot(run: StoryRunState) -> dict[str, Any]:
    """创建当前状态的快照（用于备份/回滚）。"""
    snap = asdict(run)
    snap["_snapshot_ts"] = datetime.now().isoformat()
    return snap


def restore(snapshot_data: dict[str, Any]) -> StoryRunState:
    """从快照恢复。"""
    snap = {k: v for k, v in snapshot_data.items() if not k.startswith("_")}
    run = StoryRunState(**snap)
    run.story_version += 1
    save(run)
    return run


# ── 集成到现有 story_state（兼容层） ─────────────────────


def sync_to_legacy_state(run: StoryRunState) -> dict[str, Any]:
    """将 RunState 同步到旧版 story_state dict（给旧代码用）。"""
    import story_state as ss
    legacy = ss.load_state()
    legacy["status"] = "active"
    legacy["flags"] = dict(run.flags)
    legacy["completed_beats"] = list(run.completed_beats)
    legacy["active_side_arcs"] = list(run.active_side_arcs)
    legacy["visited_branches"] = list(run.visited_branches)
    ss.save_state(legacy)
    return legacy


def sync_from_legacy_state() -> StoryRunState:
    """从旧版 story_state 同步到 RunState。"""
    import story_state as ss
    legacy = ss.load_state()
    run = load()
    if legacy.get("flags"):
        run.flags.update(legacy["flags"])
    if legacy.get("completed_beats"):
        for b in legacy["completed_beats"]:
            if b not in run.completed_beats:
                run.completed_beats.append(b)
    if legacy.get("active_side_arcs"):
        for a in legacy["active_side_arcs"]:
            if a not in run.active_side_arcs:
                run.active_side_arcs.append(a)
    if legacy.get("visited_branches"):
        for b in legacy["visited_branches"]:
            if b not in run.visited_branches:
                run.visited_branches.append(b)
    run.story_version += 1
    save(run)
    return run
