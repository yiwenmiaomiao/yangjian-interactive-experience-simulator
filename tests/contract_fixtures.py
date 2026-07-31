from __future__ import annotations

from room import contracts


def task(target: str = "yangjian") -> contracts.AgentTask:
    return contracts.AgentTask(
        task_id=f"task_{target}",
        target_agent_id=target,
        objective="Respond to the current public conversation",
        source_reference="m1",
    )
