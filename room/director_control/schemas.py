"""JSON Schemas for Hermes ctx.llm structured director calls."""

DIRECTIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "mode",
        "chapter",
        "beat",
        "observed_user_intent",
        "tasks",
        "desired_progress",
        "selected_side_arc",
        "narration",
        "npc_commands",
        "fallback_world_event",
    ],
    "properties": {
        "mode": {"const": "DIRECT"},
        "chapter": {"type": "string", "minLength": 1},
        "beat": {"type": "string", "minLength": 1},
        "observed_user_intent": {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "confidence"],
            "properties": {
                "intent": {"type": "string", "minLength": 1},
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "task_id",
                    "target",
                    "source_reference",
                    "objective",
                    "information_ids",
                    "success_condition",
                ],
                "properties": {
                    "task_id": {"type": "string", "minLength": 1},
                    "target": {"type": "string", "minLength": 1},
                    "source_reference": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "information_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "success_condition": {"type": "string", "minLength": 1},
                },
            },
        },
        "desired_progress": {
            "type": "string",
            "enum": ["maintain", "advance", "recover"],
        },
        "selected_side_arc": {
            "type": ["string", "null"],
        },
        "narration": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "required",
                "purpose",
                "timing",
                "visible_facts",
                "max_characters",
            ],
            "properties": {
                "required": {"type": "boolean"},
                "purpose": {
                    "type": "string",
                    "enum": [
                        "none",
                        "scene_opening",
                        "transition",
                        "visible_action",
                        "external_event",
                        "closing",
                    ],
                },
                "timing": {
                    "type": "string",
                    "enum": ["none", "before_dialogue", "after_dialogue"],
                },
                "visible_facts": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                },
                "max_characters": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
        },
        "npc_commands": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "command_id",
                    "operation",
                    "profile_id",
                    "npc_id",
                    "target_scene_id",
                    "reason",
                ],
                "properties": {
                    "command_id": {"type": "string", "minLength": 1},
                    "operation": {
                        "type": "string",
                        "enum": [
                            "ensure_registered",
                            "activate",
                            "deactivate",
                            "complete",
                        ],
                    },
                    "profile_id": {"type": "string", "minLength": 1},
                    "npc_id": {"type": ["string", "null"]},
                    "target_scene_id": {"type": ["string", "null"]},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
        "fallback_world_event": {"type": ["object", "null"]},
    },
}


RESOLUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "mode",
        "chapter",
        "beat",
        "decisions",
        "state_changes",
        "next_beat",
        "continuation",
    ],
    "properties": {
        "mode": {"const": "RESOLVE"},
        "chapter": {"type": "string", "minLength": 1},
        "beat": {"type": "string", "minLength": 1},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "proposal_id",
                    "result",
                    "outcome_summary",
                ],
                "properties": {
                    "proposal_id": {"type": "string", "minLength": 1},
                    "result": {
                        "type": "string",
                        "enum": [
                            "accept",
                            "modify",
                            "reject",
                            "accept_abstention",
                        ],
                    },
                    "outcome_summary": {"type": "string", "minLength": 1},
                },
            },
        },
        "state_changes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "value", "reason"],
                "properties": {
                    "key": {"type": "string", "minLength": 1},
                    "value": {},
                    "reason": {"type": "string", "minLength": 1},
                },
            },
        },
        "next_beat": {"type": ["string", "null"]},
        "continuation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "reason", "target_id", "world_event"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "continue_current",
                        "redispatch",
                        "world_event",
                        "advance",
                    ],
                },
                "reason": {"type": "string", "minLength": 1},
                "target_id": {"type": ["string", "null"]},
                "world_event": {"type": ["object", "null"]},
            },
        },
    },
}
