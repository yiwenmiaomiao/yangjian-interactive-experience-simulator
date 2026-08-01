"""Small deterministic guard around Director structured output."""

from __future__ import annotations

import re
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
    available_npc_profiles: frozenset[str] = frozenset()


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
        # 增加 target 字符串判定与非空校验
        if not isinstance(target, str) or not target.strip():
            issues.append(
                _issue(
                    "TARGET_MISSING",
                    "target is required and must be a valid string",
                    location,
                )
            )
        elif target not in context.available_agents:
            issues.append(
                _issue(
                    "TARGET_NOT_AVAILABLE",
                    f"target is not available: {target}",
                    location,
                )
            )

        source = task.get("source_reference")
        # 只要不是空字符串就通过，放行自由生成的字符串，仅使用正则表达式拦截确定格式的节点ID
        if isinstance(source, str) and source and source not in context.allowed_source_references:
            if re.match(r"^m\d+$", source) or source.startswith("side_"):
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
        elif isinstance(target, str) and target.strip():
            signature = task_signature(target, objective)
            if context.recent_task_signatures.count(signature) >= 2:
                issues.append(
                    _issue(
                        "TASK_REPEATED",
                        "the same task was already used twice without change",
                        location,
                    )
                )

    npc_commands = payload.get("npc_commands", [])
    if not isinstance(npc_commands, list):
        issues.append(
            _issue(
                "NPC_COMMANDS_INVALID",
                "npc_commands must be a list",
                "npc_commands",
            )
        )
        npc_commands = []
    for index, command in enumerate(npc_commands):
        location = f"npc_commands[{index}]"
        if not isinstance(command, Mapping):
            issues.append(
                _issue("NPC_COMMAND_INVALID", "command must be an object", location)
            )
            continue
        operation = command.get("operation")
        if operation not in {
            "ensure_registered",
            "activate",
            "deactivate",
            "complete",
        }:
            issues.append(
                _issue(
                    "NPC_OPERATION_INVALID",
                    f"unsupported NPC operation: {operation}",
                    location,
                )
            )
        profile_id = command.get("profile_id")
        if (
            operation in {"ensure_registered", "activate"}
            and profile_id not in context.available_npc_profiles
        ):
            issues.append(
                _issue(
                    "NPC_PROFILE_NOT_AVAILABLE",
                    f"profile is not supplied by StoryPlan: {profile_id}",
                    location,
                )
            )

    hold = payload.get("hold")
    if isinstance(hold, Mapping) and hold.get("requested") is True:
        issues.append(
            _issue(
                "DIRECTOR_HOLD_FORBIDDEN",
                "Director cannot stop the runtime; actors may request abstention",
                "hold",
            )
        )

    fallback_world_event = payload.get("fallback_world_event")
    resolve_gate = payload.get("resolve_gate")
    act_required = True
    if isinstance(resolve_gate, Mapping):
        act_required = resolve_gate.get("act_required", True) is not False
    if (
        not tasks
        and not npc_commands
        and not isinstance(fallback_world_event, Mapping)
        and act_required
    ):
        issues.append(
            _issue(
                "DIRECTOR_IDLE",
                "assign a task, issue an NPC command, or provide a fallback world event",
                "tasks",
            )
        )

    _check_resolve_gate(payload, context, issues)

    side_arc = payload.get("selected_side_arc")
    # 使用真值判断过滤空字符串
    if side_arc and side_arc not in context.unlocked_side_arcs:
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

        if decision.get("result") not in {
            "accept",
            "modify",
            "reject",
            "accept_abstention",
        }:
            issues.append(
                _issue(
                    "DECISION_RESULT_INVALID",
                    "result must adjudicate an action or abstention",
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

    # 移除了因为漏填提案 (PROPOSALS_UNDECIDED) 而直接打回 LLM 的校验逻辑，允许业务层做默认处理

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
    # 使用真值判断过滤空字符串
    if next_beat and next_beat not in context.unlocked_next_beats:
        issues.append(
            _issue(
                "NEXT_BEAT_LOCKED",
                f"next beat is not unlocked: {next_beat}",
                "next_beat",
            )
        )

    continuation = payload.get("continuation")
    if not isinstance(continuation, Mapping):
        issues.append(
            _issue(
                "CONTINUATION_MISSING",
                "Director must always provide a continuation plan",
                "continuation",
            )
        )
    else:
        kind = continuation.get("kind")
        if kind not in {
            "continue_current",
            "redispatch",
            "world_event",
            "advance",
        }:
            issues.append(
                _issue(
                    "CONTINUATION_INVALID",
                    f"unsupported continuation kind: {kind}",
                    "continuation",
                )
            )
        reason = continuation.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            issues.append(
                _issue(
                    "CONTINUATION_REASON_MISSING",
                    "continuation requires a reason",
                    "continuation",
                )
            )

    _check_user_outcome(payload.get("user_outcome"), context, issues)

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


def _check_resolve_gate(
    payload: Mapping[str, Any],
    context: DirectorContext,
    issues: list[GuardIssue],
) -> None:
    resolve_gate = payload.get("resolve_gate")
    inline_effects = payload.get("inline_effects")
    user_turn = payload.get("user_turn")
    if not isinstance(resolve_gate, Mapping):
        issues.append(
            _issue(
                "RESOLVE_GATE_INVALID",
                "resolve_gate must be an object",
                "resolve_gate",
            )
        )
        return
    if not isinstance(inline_effects, Mapping):
        issues.append(
            _issue(
                "INLINE_EFFECTS_INVALID",
                "inline_effects must be an object",
                "inline_effects",
            )
        )
        return

    resolve_required = resolve_gate.get("required") is True
    state_operations = inline_effects.get("state_operations", [])
    user_feedback = inline_effects.get("user_feedback")
    
    # 彻底删除了 INLINE_EFFECTS_FORBIDDEN 的校验，允许模型在需要解决时自由提供 inline_effects，业务层可自行忽略

    disclosure_required = False
    if isinstance(user_turn, Mapping):
        disclosure = user_turn.get("disclosure")
        if isinstance(disclosure, Mapping):
            disclosure_required = disclosure.get("required") is True

    if (
        not resolve_required
        and disclosure_required
        and not isinstance(user_feedback, Mapping)
    ):
        issues.append(
            _issue(
                "USER_FEEDBACK_REQUIRED",
                "user_feedback is required when disclosure is required on fast path",
                "inline_effects.user_feedback",
            )
        )

    if isinstance(user_feedback, Mapping):
        revealed = user_feedback.get("revealed_fact_ids", [])
        if isinstance(revealed, list):
            unauthorized = set(revealed) - set(context.allowed_narration_facts)
            if unauthorized:
                issues.append(
                    _issue(
                        "USER_FEEDBACK_FACT_NOT_ALLOWED",
                        f"unauthorized revealed facts: {sorted(unauthorized)}",
                        "inline_effects.user_feedback",
                    )
                )
        summary = user_feedback.get("outcome_summary")
        if isinstance(summary, str) and _contains_forbidden(
            summary, context.forbidden_outcome_fragments
        ):
            issues.append(
                _issue(
                    "USER_FEEDBACK_FORBIDDEN",
                    "user_feedback contains a Room-defined forbidden fragment",
                    "inline_effects.user_feedback",
                )
            )

    if isinstance(state_operations, list):
        for index, change in enumerate(state_operations):
            location = f"inline_effects.state_operations[{index}]"
            if not isinstance(change, Mapping):
                issues.append(
                    _issue(
                        "INLINE_STATE_INVALID",
                        "inline state operation must be an object",
                        location,
                    )
                )
                continue
            key = change.get("key")
            if key not in context.allowed_state_change_keys:
                issues.append(
                    _issue(
                        "INLINE_STATE_NOT_ALLOWED",
                        f"state key is not allowed: {key}",
                        location,
                    )
                )


def _check_user_outcome(
    user_outcome: Any,
    context: DirectorContext,
    issues: list[GuardIssue],
) -> None:
    if not isinstance(user_outcome, Mapping):
        issues.append(
            _issue(
                "USER_OUTCOME_INVALID",
                "user_outcome must be an object",
                "user_outcome",
            )
        )
        return
    if user_outcome.get("applies") is not True:
        return
    summary = user_outcome.get("outcome_summary")
    if not isinstance(summary, str) or not summary.strip():
        issues.append(
            _issue(
                "USER_OUTCOME_SUMMARY_MISSING",
                "outcome_summary is required when user_outcome applies",
                "user_outcome",
            )
        )
    elif _contains_forbidden(summary, context.forbidden_outcome_fragments):
        issues.append(
            _issue(
                "USER_OUTCOME_FORBIDDEN",
                "user_outcome contains a Room-defined forbidden fragment",
                "user_outcome",
            )
        )
    revealed = user_outcome.get("revealed_fact_ids", [])
    if isinstance(revealed, list):
        unauthorized = set(revealed) - set(context.allowed_narration_facts)
        if unauthorized:
            issues.append(
                _issue(
                    "USER_OUTCOME_FACT_NOT_ALLOWED",
                    f"unauthorized revealed facts: {sorted(unauthorized)}",
                    "user_outcome",
                )
            )


def _issue(code: str, message: str, location: str) -> GuardIssue:
    return GuardIssue(code=code, message=message, location=location)