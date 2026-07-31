"""Deterministic structural validation for generated story plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .models import MainArc, SideArc, StoryBeat, StoryPlan, StoryStandard


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationIssue:
    code: str
    message: str
    location: str
    severity: Severity = Severity.ERROR


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity is Severity.ERROR for issue in self.issues)

    def by_code(self, code: str) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code == code)


class StoryPlanValidator:
    def __init__(self, standard: StoryStandard | None = None) -> None:
        self.standard = standard or StoryStandard()

    def validate(self, plan: StoryPlan) -> ValidationReport:
        issues: list[ValidationIssue] = []

        if plan.story_standard_version != self.standard.version:
            issues.append(
                self._warning(
                    "STANDARD_VERSION_MISMATCH",
                    "Plan and validator use different story standard versions.",
                    plan.story_id,
                )
            )

        issues.extend(self._validate_arc_ids(plan))
        issues.extend(self._validate_main_arc(plan.main_arc))
        for side_arc in plan.side_arcs:
            issues.extend(self._validate_side_arc(side_arc, plan.story_id))
        issues.extend(self._validate_npc_profiles(plan))
        issues.extend(self._validate_cross_references(plan))
        issues.extend(self._validate_information_boundaries(plan))

        return ValidationReport(issues=tuple(issues))

    def _validate_arc_ids(self, plan: StoryPlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        arc_ids = [plan.main_arc.arc_id, *(arc.arc_id for arc in plan.side_arcs)]
        duplicates = self._duplicates(arc_ids)
        for arc_id in duplicates:
            issues.append(
                self._error(
                    "DUPLICATE_ARC_ID",
                    f"Arc id is duplicated: {arc_id}",
                    arc_id,
                )
            )
        return issues

    def _validate_npc_profiles(self, plan: StoryPlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        requirement_ids = {
            requirement.requirement_id
            for arc in plan.side_arcs
            for requirement in arc.npc_requirements
        }
        profile_ids = [profile.profile_id for profile in plan.npc_profiles]
        for duplicate in self._duplicates(profile_ids):
            issues.append(
                self._error(
                    "DUPLICATE_NPC_PROFILE",
                    f"NPC profile id is duplicated: {duplicate}",
                    plan.story_id,
                )
            )

        profile_requirement_ids = [
            profile.requirement_id for profile in plan.npc_profiles
        ]
        for duplicate in self._duplicates(profile_requirement_ids):
            issues.append(
                self._error(
                    "DUPLICATE_PROFILE_REQUIREMENT",
                    f"Multiple profiles target requirement: {duplicate}",
                    plan.story_id,
                )
            )

        unknown = set(profile_requirement_ids) - requirement_ids
        for requirement_id in sorted(unknown):
            issues.append(
                self._error(
                    "NPC_PROFILE_REQUIREMENT_UNKNOWN",
                    f"Profile references unknown requirement: {requirement_id}",
                    plan.story_id,
                )
            )

        missing = requirement_ids - set(profile_requirement_ids)
        for requirement_id in sorted(missing):
            issues.append(
                self._error(
                    "NPC_PROFILE_MISSING",
                    f"Requirement has no complete NPC profile: {requirement_id}",
                    plan.story_id,
                )
            )
        return issues

    def _validate_main_arc(self, arc: MainArc) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        location = f"main_arc:{arc.arc_id}"

        if not 1 <= len(arc.endings) <= self.standard.maximum_main_endings:
            issues.append(
                self._error(
                    "MAIN_ENDING_COUNT",
                    "Main arc must have one or two endings.",
                    location,
                )
            )

        participants = self._all_participants(arc.beats)
        missing = set(self.standard.required_main_participants) - participants
        if missing:
            issues.append(
                self._error(
                    "MAIN_PARTICIPANTS_MISSING",
                    f"Main arc is missing required participants: {sorted(missing)}",
                    location,
                )
            )

        if self.standard.npc_only_in_side_arcs:
            unexpected = participants - set(self.standard.required_main_participants)
            if unexpected:
                issues.append(
                    self._error(
                        "NPC_IN_MAIN_ARC",
                        f"Main arc contains non-primary participants: {sorted(unexpected)}",
                        location,
                    )
                )
            if any(beat.npc_requirement_ids for beat in arc.beats):
                issues.append(
                    self._error(
                        "NPC_REQUIREMENT_IN_MAIN_ARC",
                        "NPC requirements may only be attached to side arcs.",
                        location,
                    )
                )

        issues.extend(
            self._validate_graph(
                arc_id=arc.arc_id,
                start_beat_id=arc.start_beat_id,
                beats=arc.beats,
                ending_ids=tuple(ending.ending_id for ending in arc.endings),
            )
        )
        return issues

    def _validate_side_arc(
        self,
        arc: SideArc,
        story_id: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        location = f"side_arc:{arc.arc_id}"

        if not arc.impact_on_main_arc:
            issues.append(
                self._error(
                    "SIDE_ARC_NO_MAIN_IMPACT",
                    "A side arc must declare how it affects the main arc.",
                    location,
                )
            )

        if not 1 <= len(arc.endings) <= self.standard.maximum_side_endings:
            issues.append(
                self._error(
                    "SIDE_ENDING_COUNT",
                    (
                        "Side arc ending count must be between 1 and "
                        f"{self.standard.maximum_side_endings}."
                    ),
                    location,
                )
            )

        participants = self._all_participants(arc.beats)
        missing = set(self.standard.required_side_participants) - participants
        if missing:
            issues.append(
                self._error(
                    "SIDE_PARTICIPANTS_MISSING",
                    f"Side arc is missing required participants: {sorted(missing)}",
                    location,
                )
            )

        requirement_ids = [item.requirement_id for item in arc.npc_requirements]
        for requirement in arc.npc_requirements:
            if requirement.story_id != story_id:
                issues.append(
                    self._error(
                        "NPC_REQUIREMENT_STORY_MISMATCH",
                        (
                            f"NPC requirement belongs to {requirement.story_id}, "
                            f"expected {story_id}."
                        ),
                        f"{location}/npc:{requirement.requirement_id}",
                    )
                )
            if requirement.side_arc_id != arc.arc_id:
                issues.append(
                    self._error(
                        "NPC_REQUIREMENT_ARC_MISMATCH",
                        (
                            f"NPC requirement belongs to {requirement.side_arc_id}, "
                            f"expected {arc.arc_id}."
                        ),
                        f"{location}/npc:{requirement.requirement_id}",
                    )
                )
        for duplicate in self._duplicates(requirement_ids):
            issues.append(
                self._error(
                    "DUPLICATE_NPC_REQUIREMENT",
                    f"NPC requirement id is duplicated: {duplicate}",
                    location,
                )
            )

        requirement_id_set = set(requirement_ids)
        for beat in arc.beats:
            unknown = set(beat.npc_requirement_ids) - requirement_id_set
            if unknown:
                issues.append(
                    self._error(
                        "UNKNOWN_NPC_REQUIREMENT",
                        f"Beat references unknown NPC requirements: {sorted(unknown)}",
                        f"{location}/beat:{beat.beat_id}",
                    )
                )

        issues.extend(
            self._validate_graph(
                arc_id=arc.arc_id,
                start_beat_id=arc.start_beat_id,
                beats=arc.beats,
                ending_ids=tuple(ending.ending_id for ending in arc.endings),
            )
        )
        return issues

    def _validate_graph(
        self,
        *,
        arc_id: str,
        start_beat_id: str,
        beats: tuple[StoryBeat, ...],
        ending_ids: tuple[str, ...],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        location = f"arc:{arc_id}"
        beat_ids = [beat.beat_id for beat in beats]
        node_ids = set(beat_ids) | set(ending_ids)

        for duplicate in self._duplicates(beat_ids):
            issues.append(
                self._error(
                    "DUPLICATE_BEAT_ID",
                    f"Beat id is duplicated: {duplicate}",
                    location,
                )
            )
        for duplicate in self._duplicates(ending_ids):
            issues.append(
                self._error(
                    "DUPLICATE_ENDING_ID",
                    f"Ending id is duplicated: {duplicate}",
                    location,
                )
            )
        overlap = set(beat_ids) & set(ending_ids)
        for node_id in sorted(overlap):
            issues.append(
                self._error(
                    "NODE_ID_COLLISION",
                    f"Beat and ending share the same id: {node_id}",
                    location,
                )
            )

        if start_beat_id not in set(beat_ids):
            issues.append(
                self._error(
                    "UNKNOWN_START_BEAT",
                    f"Start beat does not exist: {start_beat_id}",
                    location,
                )
            )

        adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        transition_ids: list[str] = []
        for beat in beats:
            beat_location = f"{location}/beat:{beat.beat_id}"
            if not beat.transitions:
                issues.append(
                    self._error(
                        "DEAD_END_BEAT",
                        "A beat must transition to another beat or an ending.",
                        beat_location,
                    )
                )
            for transition in beat.transitions:
                transition_ids.append(transition.transition_id)
                if transition.target_id not in node_ids:
                    issues.append(
                        self._error(
                            "UNKNOWN_TRANSITION_TARGET",
                            (
                                f"Transition {transition.transition_id} targets "
                                f"unknown node {transition.target_id}."
                            ),
                            beat_location,
                        )
                    )
                else:
                    adjacency[beat.beat_id].add(transition.target_id)

            unknown_prerequisites = set(beat.prerequisites) - set(beat_ids)
            if unknown_prerequisites:
                issues.append(
                    self._error(
                        "UNKNOWN_PREREQUISITE",
                        (
                            "Beat references unknown prerequisites: "
                            f"{sorted(unknown_prerequisites)}"
                        ),
                        beat_location,
                    )
                )
            if beat.beat_id in beat.prerequisites:
                issues.append(
                    self._error(
                        "SELF_PREREQUISITE",
                        "A beat cannot require itself.",
                        beat_location,
                    )
                )
            if beat.reconverges_at and beat.reconverges_at not in set(beat_ids):
                issues.append(
                    self._error(
                        "UNKNOWN_RECONVERGENCE",
                        f"Unknown reconvergence beat: {beat.reconverges_at}",
                        beat_location,
                    )
                )

        for duplicate in self._duplicates(transition_ids):
            issues.append(
                self._error(
                    "DUPLICATE_TRANSITION_ID",
                    f"Transition id is duplicated: {duplicate}",
                    location,
                )
            )

        if start_beat_id in adjacency:
            reachable = self._reachable(start_beat_id, adjacency)
            unreachable = node_ids - reachable
            for node_id in sorted(unreachable):
                issues.append(
                    self._error(
                        "UNREACHABLE_NODE",
                        f"Node cannot be reached from the arc start: {node_id}",
                        location,
                    )
                )

            can_reach_ending = self._nodes_reaching_endings(adjacency, set(ending_ids))
            for beat_id in sorted(set(beat_ids) - can_reach_ending):
                issues.append(
                    self._error(
                        "NO_PATH_TO_ENDING",
                        f"Beat has no path to any ending: {beat_id}",
                        location,
                    )
                )

            if not self.standard.allow_graph_cycles:
                for cycle in self._cycles(start_beat_id, adjacency, set(ending_ids)):
                    issues.append(
                        self._error(
                            "GRAPH_CYCLE",
                            f"Story graph contains a cycle: {' -> '.join(cycle)}",
                            location,
                        )
                    )

        return issues

    def _validate_cross_references(self, plan: StoryPlan) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        all_beat_ids = {
            beat.beat_id
            for beat in (
                *plan.main_arc.beats,
                *(beat for arc in plan.side_arcs for beat in arc.beats),
            )
        }

        for item in plan.foreshadowing:
            if item.setup_beat_id not in all_beat_ids:
                issues.append(
                    self._error(
                        "UNKNOWN_FORESHADOW_SETUP",
                        f"Unknown setup beat: {item.setup_beat_id}",
                        f"foreshadowing:{item.foreshadowing_id}",
                    )
                )
            if item.payoff_beat_id not in all_beat_ids:
                issues.append(
                    self._error(
                        "UNKNOWN_FORESHADOW_PAYOFF",
                        f"Unknown payoff beat: {item.payoff_beat_id}",
                        f"foreshadowing:{item.foreshadowing_id}",
                    )
                )

        milestone_ids = set(plan.main_arc.milestones)
        for arc in plan.side_arcs:
            unknown = set(arc.unlock.required_milestones) - milestone_ids
            if unknown:
                issues.append(
                    self._error(
                        "UNKNOWN_UNLOCK_MILESTONE",
                        f"Side arc uses unknown milestones: {sorted(unknown)}",
                        f"side_arc:{arc.arc_id}",
                    )
                )
        return issues

    def _validate_information_boundaries(
        self, plan: StoryPlan
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        all_beats = (
            *plan.main_arc.beats,
            *(beat for arc in plan.side_arcs for beat in arc.beats),
        )
        for beat in all_beats:
            leaked = set(beat.allowed_information) & set(beat.forbidden_reveals)
            if leaked:
                issues.append(
                    self._error(
                        "INFORMATION_BOUNDARY_CONFLICT",
                        (
                            "Information is both allowed and forbidden: "
                            f"{sorted(leaked)}"
                        ),
                        f"beat:{beat.beat_id}",
                    )
                )
        for secret in plan.secrets:
            leaked_to = set(secret.known_by) & set(secret.never_reveal_to)
            if leaked_to:
                issues.append(
                    self._error(
                        "SECRET_AUDIENCE_CONFLICT",
                        (
                            "Secret is known by an explicitly forbidden audience: "
                            f"{sorted(leaked_to)}"
                        ),
                        f"secret:{secret.secret_id}",
                    )
                )
        return issues

    @staticmethod
    def _all_participants(beats: Iterable[StoryBeat]) -> set[str]:
        return {participant for beat in beats for participant in beat.participants}

    @staticmethod
    def _duplicates(values: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for value in values:
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        return duplicates

    @staticmethod
    def _reachable(start: str, adjacency: dict[str, set[str]]) -> set[str]:
        visited: set[str] = set()
        pending = [start]
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            pending.extend(adjacency.get(node, ()))
        return visited

    @staticmethod
    def _nodes_reaching_endings(
        adjacency: dict[str, set[str]], endings: set[str]
    ) -> set[str]:
        reverse: dict[str, set[str]] = {node: set() for node in adjacency}
        for source, targets in adjacency.items():
            for target in targets:
                reverse.setdefault(target, set()).add(source)

        reachable = set(endings)
        pending = list(endings)
        while pending:
            node = pending.pop()
            for parent in reverse.get(node, ()):
                if parent not in reachable:
                    reachable.add(parent)
                    pending.append(parent)
        return reachable

    @staticmethod
    def _cycles(
        start: str,
        adjacency: dict[str, set[str]],
        terminal_nodes: set[str],
    ) -> list[tuple[str, ...]]:
        visited: set[str] = set()
        active: list[str] = []
        active_set: set[str] = set()
        cycles: list[tuple[str, ...]] = []

        def visit(node: str) -> None:
            if node in terminal_nodes:
                return
            if node in active_set:
                index = active.index(node)
                cycle = tuple((*active[index:], node))
                if cycle not in cycles:
                    cycles.append(cycle)
                return
            if node in visited:
                return

            active.append(node)
            active_set.add(node)
            for target in adjacency.get(node, ()):
                visit(target)
            active.pop()
            active_set.remove(node)
            visited.add(node)

        visit(start)
        return cycles

    @staticmethod
    def _error(code: str, message: str, location: str) -> ValidationIssue:
        return ValidationIssue(code=code, message=message, location=location)

    @staticmethod
    def _warning(code: str, message: str, location: str) -> ValidationIssue:
        return ValidationIssue(
            code=code,
            message=message,
            location=location,
            severity=Severity.WARNING,
        )
