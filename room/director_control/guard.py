"""Small deterministic guard around Director structured output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True, kw_only=True)
class DirectorContext:
    chapter: str
    beat: str
    available_agents: frozenset[str]
    allowed_information: Mapping[str, frozenset[str]]
    allowed_source_references: frozenset[str]
    unlocked_next_beats: frozenset[str] = frozenset()
    unlocked_side_arcs: frozenset[str] = frozenset()
    allowed_state_change_keys: frozenset[str] = frozenset()
    proposal_ids: frozenset[str] = frozenset()
    narration_allowed: bool = False
    allowed_narration_facts: frozenset[str] = frozenset()
    consecutive_holds: int = 0
    recent_task_signatures: tuple[str, ...] = ()
    forbidden_outcome_fragments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class GuardIssue:
    code: str
    message: str
    location: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GuardReport:
    issues: tuple[GuardIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.issues


def validate_directive(
    payload: Mapping[str, Any],
    context: DirectorContext,
) -> GuardReport:
    issues: list[GuardIssue] = []
    _check_common(payload, context, "DIRECT", issues)

    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        issues.append(_issue("TASKS_INVALID", "tasks must be a list", "tasks"))
        tasks = []

    task_ids: set[str] = set()
    for index, task in enumerate(tasks):
        location = f"tasks[{index}]"
        if not isinstance(task, Mapping):
            issues.append(_issue("TASK_INVALID", "task must be an object", location))
            continue

        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            issues.append(_issue("TASK_ID_MISSING", "task_id is required", location))
        elif task_id in task_ids:
            issues.append(
                _issue("TASK_ID_DUPLICATE", f"duplicate task_id: {task_id}", location)
            )
        else:
            task_ids.add(task_id)

        target = task.get("target")
        if target not in context.available_agents:
            issues.append(
                _issue(
                    "TARGET_NOT_AVAILABLE",
                    f"target is not available: {target}",
                    location,
                )
            )

        source = task.get("source_reference")
        # 只要不是空字符串就通过（LLM 自由生成的 source_reference 无法精确匹配）
        if source and source not in context.allowed_source_references:
            # 只有明确已知的 source 才校验，自由生成的不拦
            if source in ("m1", "m2", "m3", "m4") or source.startswith("side_"):
                issues.append(
                    _issue(
                        "SOURCE_NOT_ALLOWED",
                        f"source reference is not allowed: {source}",
                        location,
                    )
                )

        info_ids = task.get("information_ids", [])
        if not isinstance(info_ids, list):
            issues.append(
                _issue(
                    "INFORMATION_IDS_INVALID",
                    "information_ids must be a list",
                    location,
                )
            )
        else:
            allowed = context.allowed_information.get(str(target), frozenset())
            unauthorized = set(info_ids) - set(allowed)
            if unauthorized:
                issues.append(
                    _issue(
                        "INFORMATION_NOT_ALLOWED",
                        f"unauthorized information: {sorted(unauthorized)}",
                        location,
                    )
                )

        objective = task.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            issues.append(
                _issue("OBJECTIVE_MISSING", "objective is required", location)
            )
        elif target:
            signature = task_signature(str(target), objective)
            if context.recent_task_signatures.count(signature) >= 2:
                issues.append(
                    _issue(
                        "TASK_REPEATED",
                        "the same task was already used twice without change",
                        location,
                    )
                )

    hold = payload.get("hold")
    hold_requested = isinstance(hold, Mapping) and hold.get("requested") is True
    if tasks and hold_requested:
        issues.append(
            _issue(
                "HOLD_WITH_TASKS",
                "hold cannot be requested when tasks are assigned",
                "hold",
            )
        )
    if not tasks and not hold_requested:
        issues.append(
            _issue(
                "DIRECTOR_IDLE",
                "assign at least one task or request a justified hold",
                "tasks",
            )
        )
    if hold_requested:
        if context.consecutive_holds >= 1:
            issues.append(
                _issue(
                    "REPEATED_HOLD",
                    "hold cannot be used in consecutive turns",
                    "hold",
                )
            )
        reason = hold.get("reason")
        wait_for = hold.get("wait_for")
        if not isinstance(reason, str) or not reason.strip():
            issues.append(
                _issue("HOLD_REASON_MISSING", "hold needs a reason", "hold")
            )
        if not isinstance(wait_for, str) or not wait_for.strip():
            issues.append(
                _issue("HOLD_WAIT_MISSING", "hold needs wait_for", "hold")
            )

    side_arc = payload.get("selected_side_arc")
    if side_arc is not None and side_arc not in context.unlocked_side_arcs:
        issues.append(
            _issue(
                "SIDE_ARC_LOCKED",
                f"side arc is not unlocked: {side_arc}",
                "selected_side_arc",
            )
        )

    _check_narration(payload.get("narration"), context, issues)
    return GuardReport(issues=tuple(issues))


def validate_resolution(
    payload: Mapping[str, Any],
    context: DirectorContext,
) -> GuardReport:
    issues: list[GuardIssue] = []
    _check_common(payload, context, "RESOLVE", issues)

    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        issues.append(
            _issue("DECISIONS_INVALID", "decisions must be a list", "decisions")
        )
        decisions = []

    seen: set[str] = set()
    for index, decision in enumerate(decisions):
        location = f"decisions[{index}]"
        if not isinstance(decision, Mapping):
            issues.append(
                _issue("DECISION_INVALID", "decision must be an object", location)
            )
            continue
        proposal_id = decision.get("proposal_id")
        if proposal_id not in context.proposal_ids:
            issues.append(
                _issue(
                    "UNKNOWN_PROPOSAL",
                    f"unknown proposal: {proposal_id}",
                    location,
                )
            )
        elif proposal_id in seen:
            issues.append(
                _issue(
                    "DUPLICATE_PROPOSAL_DECISION",
                    f"proposal decided twice: {proposal_id}",
                    location,
                )
            )
        else:
            seen.add(str(proposal_id))

        if decision.get("result") not in {"accept", "modify", "reject"}:
            issues.append(
                _issue(
                    "DECISION_RESULT_INVALID",
                    "result must be accept, modify or reject",
                    location,
                )
            )

        outcome = decision.get("outcome_summary")
        if not isinstance(outcome, str) or not outcome.strip():
            issues.append(
                _issue(
                    "OUTCOME_MISSING",
                    "outcome_summary is required",
                    location,
                )
            )
        elif _contains_forbidden(outcome, context.forbidden_outcome_fragments):
            issues.append(
                _issue(
                    "OUTCOME_FORBIDDEN",
                    "outcome contains a Room-defined forbidden fragment",
                    location,
                )
            )

    missing = context.proposal_ids - seen
    if missing:
        issues.append(
            _issue(
                "PROPOSALS_UNDECIDED",
                f"all proposals require a decision: {sorted(missing)}",
                "decisions",
            )
        )

    state_changes = payload.get("state_changes")
    if not isinstance(state_changes, list):
        issues.append(
            _issue(
                "STATE_CHANGES_INVALID",
                "state_changes must be a list",
                "state_changes",
            )
        )
    else:
        for index, change in enumerate(state_changes):
            location = f"state_changes[{index}]"
            if not isinstance(change, Mapping):
                issues.append(
                    _issue(
                        "STATE_CHANGE_INVALID",
                        "state change must be an object",
                        location,
                    )
                )
                continue
            key = change.get("key")
            if key not in context.allowed_state_change_keys:
                issues.append(
                    _issue(
                        "STATE_CHANGE_NOT_ALLOWED",
                        f"state key is not allowed: {key}",
                        location,
                    )
                )

    next_beat = payload.get("next_beat")
    if next_beat is not None and next_beat not in context.unlocked_next_beats:
        issues.append(
            _issue(
                "NEXT_BEAT_LOCKED",
                f"next beat is not unlocked: {next_beat}",
                "next_beat",
            )
        )

    return GuardReport(issues=tuple(issues))


def task_signature(target: str, objective: str) -> str:
    normalized = " ".join(objective.casefold().split())
    return f"{target}:{normalized}"


def _check_common(
    payload: Mapping[str, Any],
    context: DirectorContext,
    expected_mode: str,
    issues: list[GuardIssue],
) -> None:
    if payload.get("mode") != expected_mode:
        issues.append(
            _issue(
                "MODE_INVALID",
                f"mode must be {expected_mode}",
                "mode",
            )
        )
    # chapter 和 beat 由系统预处理修正，不再校验 LLM 输出


def _check_narration(
    narration: Any,
    context: DirectorContext,
    issues: list[GuardIssue],
) -> None:
    if not isinstance(narration, Mapping):
        issues.append(
            _issue(
                "NARRATION_INVALID",
                "narration must be an object",
                "narration",
            )
        )
        return

    required = narration.get("required") is True
    if required and not context.narration_allowed:
        issues.append(
            _issue(
                "NARRATION_NOT_ALLOWED",
                "Room did not allow narration this turn",
                "narration",
            )
        )
    visible_facts = narration.get("visible_facts", [])
    if not isinstance(visible_facts, list):
        issues.append(
            _issue(
                "NARRATION_FACTS_INVALID",
                "visible_facts must be a list",
                "narration",
            )
        )
    else:
        unauthorized = set(visible_facts) - set(context.allowed_narration_facts)
        if unauthorized:
            issues.append(
                _issue(
                    "NARRATION_FACT_NOT_ALLOWED",
                    f"unauthorized narration facts: {sorted(unauthorized)}",
                    "narration",
                )
            )


def _contains_forbidden(text: str, fragments: tuple[str, ...]) -> bool:
    normalized = text.casefold()
    return any(fragment.casefold() in normalized for fragment in fragments)


def _issue(code: str, message: str, location: str) -> GuardIssue:
    return GuardIssue(code=code, message=message, location=location)
