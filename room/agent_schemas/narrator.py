"""Narrator structured output."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NarrationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    location: str | None = Field(
        default=None,
        description="如果旁白描述了一个明确的地点（如'密室'、'庭院'），填写该地点名；否则填 null",
    )
