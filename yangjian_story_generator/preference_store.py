"""
Preference Store — Hermes 用户偏好记录、聚合和导出模块

位置：属于 Hermes（默认 profile），但数据结构和导出逻辑在此定义。
偏好快照以 JSON 文件导出，story_generator 读取。

使用方式（在 Hermes/TUI 中）：
  from yangjian_story_generator.preference_store import PreferenceStore
  store = PreferenceStore()
  store.record_signal(...)
  store.export_snapshot()
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .models import PreferenceSnapshot, PreferenceMeasure


# ── 信号类型 ──────────────────────────────────────────────────


class SignalSource(StrEnum):
    EXPLICIT_SETTING = "explicit_setting"
    EXPLICIT_FEEDBACK = "explicit_feedback"
    EXPLICIT_BOUNDARY = "explicit_boundary"
    REPEATED_BEHAVIOR = "repeated_behavior"
    SINGLE_BEHAVIOR = "single_behavior"
    HERMES_INFERENCE = "hermes_inference"


# 基础权重
SIGNAL_BASE_WEIGHTS: dict[SignalSource, float] = {
    SignalSource.EXPLICIT_BOUNDARY: 1.0,
    SignalSource.EXPLICIT_SETTING: 1.0,
    SignalSource.EXPLICIT_FEEDBACK: 0.9,
    SignalSource.REPEATED_BEHAVIOR: 0.6,
    SignalSource.SINGLE_BEHAVIOR: 0.3,
    SignalSource.HERMES_INFERENCE: 0.2,
}


@dataclass
class PreferenceSignal:
    """单次偏好信号。"""
    signal_id: str
    user_id: str
    observed_at: str
    source_type: SignalSource
    dimension: str
    direction: str  # increase / decrease / avoid / neutral
    strength: float = 0.5
    confidence: float = 0.5
    context: dict[str, Any] = field(default_factory=dict)
    evidence_summary: str = ""
    raw_reference: str = ""
    scope: str = "global"
    expires_at: str | None = None


# ── 聚合偏好 ──────────────────────────────────────────────────


@dataclass
class AggregatedPreference:
    """聚合后的偏好值。"""
    value: float
    confidence: float
    source_ids: list[str]
    updated_at: str


DEFAULT_STORE_PATH = os.path.expanduser("/Users/xiaoxianhan/Documents/yangjian-room/contexts/preference_signals.json")
DEFAULT_SNAPSHOT_PATH = os.path.expanduser("/Users/xiaoxianhan/Documents/yangjian-room/contexts/preference_snapshot.json")


class PreferenceStore:
    """偏好信号存储器与聚合器。"""

    def __init__(self, store_path: str | None = None) -> None:
        self._store_path = store_path or DEFAULT_STORE_PATH
        self._signals: list[PreferenceSignal] = []
        self._profile_version = 0
        self._last_update: str | None = None
        self._load()

    # ── 信号记录 ──────────────────────────────────────────

    def _now(self) -> str:
        return datetime.now().isoformat()

    def record_signal(
        self,
        *,
        dimension: str,
        direction: str,
        source_type: SignalSource = SignalSource.HERMES_INFERENCE,
        strength: float = 0.5,
        confidence: float = 0.5,
        evidence_summary: str = "",
        context: dict[str, Any] | None = None,
        scope: str = "global",
    ) -> str:
        """记录一条偏好信号。"""
        signal_id = f"sig_{self._now()}_{len(self._signals)}"
        signal = PreferenceSignal(
            signal_id=signal_id,
            user_id="xiaoxianhan",
            observed_at=self._now(),
            source_type=source_type,
            dimension=dimension,
            direction=direction,
            strength=strength,
            confidence=confidence,
            context=context or {},
            evidence_summary=evidence_summary,
            scope=scope,
        )
        self._signals.append(signal)
        self._save()
        return signal_id

    def record_boundary(
        self,
        *,
        topic: str,
        evidence_summary: str = "",
    ) -> str:
        """记录硬性边界信号。"""
        return self.record_signal(
            dimension=f"boundary.{topic}",
            direction="avoid",
            source_type=SignalSource.EXPLICIT_BOUNDARY,
            strength=1.0,
            confidence=1.0,
            evidence_summary=evidence_summary,
        )

    def record_feedback(
        self,
        *,
        dimension: str,
        direction: str,
        strength: float = 0.8,
        evidence_summary: str = "",
    ) -> str:
        """记录明确反馈。"""
        return self.record_signal(
            dimension=dimension,
            direction=direction,
            source_type=SignalSource.EXPLICIT_FEEDBACK,
            strength=strength,
            confidence=0.9,
            evidence_summary=evidence_summary,
        )

    # ── 聚合 ────────────────────────────────────────────

    def _aggregate_dimension(self, dimension: str) -> AggregatedPreference:
        """聚合某个维度的所有信号。"""
        relevant = [s for s in self._signals if s.dimension == dimension]
        if not relevant:
            return AggregatedPreference(value=0.0, confidence=0.0, source_ids=[], updated_at=self._now())

        # 最近信号排序
        relevant.sort(key=lambda s: s.observed_at, reverse=True)

        total_weight = 0.0
        weighted_sum = 0.0
        source_ids: list[str] = []

        for s in relevant:
            base_weight = SIGNAL_BASE_WEIGHTS.get(s.source_type, 0.3)
            # 时间衰减：越旧的信号权重越低（硬边界不做衰减）
            if s.source_type == SignalSource.EXPLICIT_BOUNDARY:
                time_factor = 1.0
            else:
                age_days = self._days_since(s.observed_at)
                time_factor = max(0.3, 1.0 - age_days * 0.05)  # 20天衰减到0

            weight = base_weight * s.strength * s.confidence * time_factor
            total_weight += weight

            if s.direction == "increase":
                weighted_sum += weight * s.strength
            elif s.direction == "decrease":
                weighted_sum -= weight * s.strength
            elif s.direction == "avoid":
                weighted_sum -= weight * s.strength  # 负向
            # "neutral" 不改变

            source_ids.append(s.signal_id)

        if total_weight == 0:
            return AggregatedPreference(value=0.0, confidence=0.0, source_ids=source_ids, updated_at=self._now())

        value = max(-1.0, min(1.0, weighted_sum / total_weight))
        confidence = min(1.0, total_weight / (len(relevant) * 1.0))

        return AggregatedPreference(
            value=(value + 1.0) / 2.0,  # 将 -1~1 映射到 0~1
            confidence=confidence,
            source_ids=source_ids,
            updated_at=relevant[0].observed_at,
        )

    def _days_since(self, date_str: str) -> float:
        try:
            dt = datetime.fromisoformat(date_str)
            return (datetime.now() - dt).total_seconds() / 86400.0
        except (ValueError, TypeError):
            return 0.0

    # ── 导出 ────────────────────────────────────────────

    def collect_dimensions(self) -> list[str]:
        """收集所有有信号的维度。"""
        return list(set(s.dimension for s in self._signals))

    def export_snapshot(self) -> PreferenceSnapshot:
        """导出当前偏好快照给故事生成器。"""
        self._profile_version += 1
        now = self._now()

        measures: dict[str, PreferenceMeasure] = {}
        hard_avoid: list[str] = []
        soft_avoid: list[str] = []

        for dim in self.collect_dimensions():
            agg = self._aggregate_dimension(dim)
            measures[dim] = PreferenceMeasure(
                value=round(agg.value, 3),
                confidence=round(agg.confidence, 3),
                updated_at=agg.updated_at,
                source_ids=tuple(agg.source_ids),
            )
            if dim.startswith("boundary."):
                hard_avoid.append(dim.replace("boundary.", ""))

        snapshot = PreferenceSnapshot(
            user_id="xiaoxianhan",
            profile_version=self._profile_version,
            created_at=now,
            measures=measures,
            hard_avoid=tuple(hard_avoid),
        )

        self._last_update = now
        self._save_snapshot(snapshot)
        return snapshot

    def _save_snapshot(self, snapshot: PreferenceSnapshot) -> None:
        """保存偏好快照到文件。"""
        from dataclasses import asdict
        path = DEFAULT_SNAPSHOT_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(snapshot), f, ensure_ascii=False, indent=2)

    # ── 持久化 ──────────────────────────────────────────

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        with open(self._store_path, "w", encoding="utf-8") as f:
            json.dump([
                {
                    "signal_id": s.signal_id,
                    "user_id": s.user_id,
                    "observed_at": s.observed_at,
                    "source_type": s.source_type.value,
                    "dimension": s.dimension,
                    "direction": s.direction,
                    "strength": s.strength,
                    "confidence": s.confidence,
                    "context": s.context,
                    "evidence_summary": s.evidence_summary,
                    "scope": s.scope,
                }
                for s in self._signals
            ], f, ensure_ascii=False, indent=2)

    def _load(self) -> None:
        if not os.path.exists(self._store_path):
            return
        with open(self._store_path, encoding="utf-8") as f:
            raw_list = json.load(f)
        for item in raw_list:
            self._signals.append(PreferenceSignal(
                signal_id=item["signal_id"],
                user_id=item["user_id"],
                observed_at=item["observed_at"],
                source_type=SignalSource(item["source_type"]),
                dimension=item["dimension"],
                direction=item["direction"],
                strength=item.get("strength", 0.5),
                confidence=item.get("confidence", 0.5),
                context=item.get("context", {}),
                evidence_summary=item.get("evidence_summary", ""),
                scope=item.get("scope", "global"),
            ))

    def list_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        """列出最近信号（用于查看/调试）。"""
        recent = sorted(self._signals, key=lambda s: s.observed_at, reverse=True)[:limit]
        return [
            {
                "signal_id": s.signal_id,
                "observed_at": s.observed_at,
                "source_type": s.source_type.value,
                "dimension": s.dimension,
                "direction": s.direction,
                "strength": s.strength,
                "confidence": s.confidence,
                "evidence_summary": s.evidence_summary[:80] if s.evidence_summary else "",
            }
            for s in recent
        ]

    def snapshot_status(self) -> dict[str, Any]:
        """查看当前偏好快照状态。"""
        return {
            "total_signals": len(self._signals),
            "dimensions": self.collect_dimensions(),
            "profile_version": self._profile_version,
            "last_update": self._last_update,
        }
