"""JSON Schemas derived from Pydantic agent output models."""

from __future__ import annotations

from agent_schemas.director import (
    DirectorDirectiveOutput,
    DirectorResolutionOutput,
)

DIRECTIVE_SCHEMA = DirectorDirectiveOutput.model_json_schema()
RESOLUTION_SCHEMA = DirectorResolutionOutput.model_json_schema()
