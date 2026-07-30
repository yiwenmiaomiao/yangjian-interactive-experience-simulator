"""NPC Manager subpackage — lifecycle, registry, permissions, and runtime for NPC agents.

Architecture:
  Director decides → Room coordinates → NPC Manager manages → NPC agents act

  NPCRequirement lives in yangjian_story_generator.models as the shared bridge.
"""
from .codec import npc_record_from_dict, npc_record_from_json, npc_record_to_json
from .lifecycle import (
    InvalidLifecycleTransition,
    TransitionTrigger,
    transition,
)
from .manager import (
    NPCIntegrationPendingError,
    NPCManager,
    NPCNotFoundError,
)
from .models import (
    AcceptedNPCEvent,
    DirectorTask,
    ManagerMetrics,
    NarrativeFunction,
    NPCMemory,
    NPCProfile,
    NPCProposal,
    NPCRecord,
    NPCStatus,
    NPCTurnContext,
    TaskSource,
)
from .permissions import (
    ProposalValidation,
    build_turn_context,
    validate_proposal,
)
from .prompting import (
    NPC_BASE_SYSTEM_PROMPT,
    NPC_PROPOSAL_SCHEMA,
    build_npc_turn_input,
    build_npc_turn_input_json,
)
from .registry import (
    InMemoryNPCRepository,
    ReuseCandidate,
    find_reuse_candidates,
)

# NPCRequirement is the shared bridge with story_generator
from yangjian_story_generator.models import NPCRequirement

__all__ = [
    "AcceptedNPCEvent",
    "DirectorTask",
    "InMemoryNPCRepository",
    "InvalidLifecycleTransition",
    "ManagerMetrics",
    "NPCIntegrationPendingError",
    "NPC_BASE_SYSTEM_PROMPT",
    "NPC_PROPOSAL_SCHEMA",
    "NPCManager",
    "NPCMemory",
    "NPCNotFoundError",
    "NPCProfile",
    "NPCProposal",
    "NPCRecord",
    "NPCRequirement",
    "NPCStatus",
    "NPCTurnContext",
    "NarrativeFunction",
    "ProposalValidation",
    "ReuseCandidate",
    "TaskSource",
    "TransitionTrigger",
    "build_turn_context",
    "build_npc_turn_input",
    "build_npc_turn_input_json",
    "find_reuse_candidates",
    "npc_record_from_dict",
    "npc_record_from_json",
    "npc_record_to_json",
    "transition",
    "validate_proposal",
]
