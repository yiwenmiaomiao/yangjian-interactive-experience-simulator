"""Review report dataclasses and exceptions."""
from __future__ import annotations

from .story_reviewer import ReviewIssue, ReviewReport


class GeneratedPlanReviewError(Exception):
    """Raised when the LLM reviewer reports critical errors in a generated plan."""

    def __init__(self, report: ReviewReport) -> None:
        self.report = report
        lines = [
            f"Story plan {report.story_id!r} failed LLM review:",
        ]
        for issue in report.errors:
            lines.append(f"  [{issue.severity.upper()}] {issue.category}/{issue.beat_id}: {issue.message}")
        super().__init__("\n".join(lines))
