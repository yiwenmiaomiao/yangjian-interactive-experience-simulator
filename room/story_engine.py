"""
故事线引擎
职责：按阶段从故事文件提取当前和下一阶段内容
"""
import os, re, json

PROFILE_DIR = os.path.abspath(os.path.expanduser(os.environ.get(
    "YANGJIAN_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)))
STORIES_DIR = os.path.join(PROFILE_DIR, "stories")

PHASE_HEADER_RE = re.compile(r"^## Phase (\d+)", re.MULTILINE)


def list_stories():
    """返回所有故事线文件名列表"""
    files = []
    for f in os.listdir(STORIES_DIR):
        if f.endswith(".md") and f != "README.md":
            files.append(f.replace(".md", ""))
    return files


def read_story(name):
    """读取完整故事线内容"""
    path = os.path.join(STORIES_DIR, f"{name}.md")
    if not os.path.exists(path):
        return {"name": name, "error": "not found"}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"name": name, "content": content}


def get_phase_boundaries(content):
    """
    返回所有 phase 边界：[{"phase": 0, "start": 0, "end": N}, ...]
    """
    matches = list(PHASE_HEADER_RE.finditer(content))
    boundaries = []
    for i, m in enumerate(matches):
        phase = int(m.group(1))
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        boundaries.append({"phase": phase, "start": start, "end": end})
    return boundaries


def extract_phase(content, phase_num):
    """提取指定 phase 的文本内容"""
    boundaries = get_phase_boundaries(content)
    for b in boundaries:
        if b["phase"] == phase_num:
            return content[b["start"]:b["end"]].strip()
    return None


def extract_goals(content, phase_num):
    """从 phase 内容中提取阶段目标"""
    phase_content = extract_phase(content, phase_num)
    if not phase_content:
        return {}
    
    goals = {}
    in_goals = False
    current_role = None
    
    for line in phase_content.split("\n"):
        if line.startswith("## 阶段目标"):
            in_goals = True
            continue
        if in_goals and line.startswith("## "):
            break
        if in_goals and line.startswith("**") and "**" in line[2:]:
            role = line.strip("*").strip(" :")
            current_role = role
            goals[role] = ""
        elif in_goals and current_role and line.strip():
            if goals.get(current_role):
                goals[current_role] += " " + line.strip()
            else:
                goals[current_role] = line.strip()
    
    return goals


def extract_phase_range(content, from_phase, to_phase=None):
    """提取 from_phase 到 to_phase 的文本"""
    boundaries = get_phase_boundaries(content)
    result = []
    for b in boundaries:
        if b["phase"] >= from_phase:
            if to_phase is not None and b["phase"] > to_phase:
                break
            result.append(content[b["start"]:b["end"]].strip())
    return "\n\n---\n\n".join(result)


def get_active_stories(state):
    """
    返回所有活跃（triggered 或 phase > 0）的故事线当前阶段内容。
    返回格式：{ "story_name": { "phase": N, "content": "...", "total_phases": M } }
    """
    active = {}
    for story_key, story_data in state.get("stories", {}).items():
        if story_data.get("triggered") or story_data.get("phase", 0) > 0:
            story = read_story(story_key)
            if "error" in story:
                continue
            content = story["content"]
            boundaries = get_phase_boundaries(content)
            phase = story_data.get("phase", 0)
            phase_content = extract_phase(content, phase)
            next_phase = extract_phase(content, phase + 1) if phase + 1 < len(boundaries) else None
            active[story_key] = {
                "phase": phase,
                "total_phases": len(boundaries),
                "current_content": phase_content,
                "next_content": next_phase,
                "goals": extract_goals(content, phase),
                "ticks_since_advance": story_data.get("ticks_stalled", 0),
            }
    return active


def get_story_summary(state):
    """
    返回故事线摘要（给导演用）：
    每条故事线的阶段 + 标题
    """
    lines = []
    for story_key, story_data in state.get("stories", {}).items():
        story = read_story(story_key)
        if "error" in story:
            continue
        # 提取第一行作为标题
        first_line = story["content"].strip().split("\n")[0].replace("#", "").strip()
        phase = story_data.get("phase", 0)
        triggered = story_data.get("triggered", False)
        status = "进行中" if triggered else "未触发"
        lines.append(f"  {story_key}: 「{first_line}」 Phase {phase} — {status}")
    return "\n".join(lines)
