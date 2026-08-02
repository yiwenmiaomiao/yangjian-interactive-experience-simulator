"""In-memory repository and deterministic NPC reuse filtering."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

from yangjian_story_generator.models import NPCRequirement
from .models import NPCRecord, NPCStatus


class InMemoryNPCRepository:
    """Test and migration-friendly repository with no database dependency."""

    def __init__(self, records: tuple[NPCRecord, ...] = ()) -> None:
        self._records = {record.profile.npc_id: record for record in records}

    def get(self, npc_id: str) -> NPCRecord | None:
        return self._records.get(npc_id)

    def list_all(self) -> tuple[NPCRecord, ...]:
        return tuple(self._records.values())

    def save(self, record: NPCRecord) -> None:
        self._records[record.profile.npc_id] = record


class JsonNPCRepository:
    """Small atomic JSON repository for durable NPC identity and memory."""

    def __init__(self, path: str) -> None:
        self._path = path

    def _load(self) -> dict[str, NPCRecord]:
        if not os.path.exists(self._path):
            return {}
        from .codec import npc_record_from_dict
        with open(self._path, encoding="utf-8") as handle:
            data = json.load(handle)
        return {
            npc_id: npc_record_from_dict(record)
            for npc_id, record in data.items()
        }

    def get(self, npc_id: str) -> NPCRecord | None:
        return self._load().get(npc_id)

    def list_all(self) -> tuple[NPCRecord, ...]:
        return tuple(self._load().values())

    def save(self, record: NPCRecord) -> None:
        from dataclasses import asdict
        records = self._load()
        records[record.profile.npc_id] = record
        parent = os.path.dirname(self._path)
        os.makedirs(parent, exist_ok=True)
        payload = {
            npc_id: asdict(item)
            for npc_id, item in records.items()
        }
        fd, temporary = tempfile.mkstemp(dir=parent, prefix="npc_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReuseCandidate:
    record: NPCRecord
    score: int
    reasons: tuple[str, ...]
    semantic_review_recommended: bool


def find_reuse_candidates(
    requirement: NPCRequirement,
    records: tuple[NPCRecord, ...],
) -> tuple[ReuseCandidate, ...]:
    candidates: list[ReuseCandidate] = []

    for record in records:
        profile = record.profile
        if not profile.reusable or profile.permanently_unavailable:
            continue
        if profile.status is NPCStatus.ACTIVE:
            continue
        if set(profile.knows) & set(requirement.must_not_know):
            continue

        score = 0
        reasons: list[str] = []
        if requirement.narrative_function in profile.supported_functions:
            score += 3
            reasons.append("narrative_function")
        if _same_text(profile.short_background, requirement.npc_background):
            score += 2
            reasons.append("background")
        if _same_text(
            profile.relation_to_yangjian,
            requirement.relation_to_yangjian,
        ):
            score += 1
            reasons.append("relation_to_yangjian")
        if _same_text(profile.relation_to_user, requirement.relation_to_user):
            score += 1
            reasons.append("relation_to_user")

        if score == 0:
            continue

        candidates.append(
            ReuseCandidate(
                record=record,
                score=score,
                reasons=tuple(reasons),
                semantic_review_recommended=score < 3,
            )
        )

    return tuple(
        sorted(
            candidates,
            key=lambda item: (-item.score, item.record.profile.npc_id),
        )
    )


def _same_text(left: str, right: str) -> bool:
    return bool(left.strip() and right.strip()) and (
        left.strip().casefold() == right.strip().casefold()
    )
