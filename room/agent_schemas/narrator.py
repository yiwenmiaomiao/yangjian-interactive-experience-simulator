"""Narrator structured output."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class NarrationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
