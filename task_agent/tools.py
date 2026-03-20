"""Tool definitions for Task Agent LLM calls.

Each tool is defined in OpenAI function-calling format. Tool execution is
delegated to registered handlers (plugged in by Kernel at Task 1.5).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from logging_system import get_logger

# Handler signature: async (tool_name, arguments_dict) -> result_dict
ToolHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "start_job",
            "description": "Create and start a new Job. expert_type must be one of: ReconExpert, CombatExpert, MovementExpert, DeployExpert, EconomyExpert. config must use the exact field names defined for each Expert type (see system prompt).",
            "parameters": {
                "type": "object",
                "properties": {
                    "expert_type": {
                        "type": "string",
                        "enum": ["ReconExpert", "CombatExpert", "MovementExpert", "DeployExpert", "EconomyExpert"],
                        "description": "The Expert type to instantiate.",
                    },
                    "config": {
                        "type": "object",
                        "description": "Expert-specific configuration. Schema depends on expert_type.",
                    },
                },
                "required": ["expert_type", "config"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_job",
            "description": "Modify parameters of a running Job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The Job to modify."},
                    "params": {"type": "object", "description": "Parameters to update."},
                },
                "required": ["job_id", "params"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_job",
            "description": "Pause a running Job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The Job to pause."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_job",
            "description": "Resume a paused Job.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The Job to resume."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "abort_job",
            "description": "Terminate a Job immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The Job to terminate."},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark the current Task as completed with a final status and summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "enum": ["succeeded", "failed", "partial"],
                        "description": "Final result of the Task.",
                    },
                    "summary": {"type": "string", "description": "Summary of what was accomplished."},
                },
                "required": ["result", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_constraint",
            "description": "Create a behavioral constraint for Jobs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "Constraint type (e.g. do_not_chase, economy_first, defend_base)."},
                    "scope": {"type": "string", "description": "Scope: global / expert_type:X / task_id:X."},
                    "params": {"type": "object", "description": "Constraint parameters."},
                    "enforcement": {
                        "type": "string",
                        "enum": ["clamp", "escalate"],
                        "description": "Enforcement mode.",
                    },
                },
                "required": ["kind", "scope", "params", "enforcement"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_constraint",
            "description": "Remove an active constraint.",
            "parameters": {
                "type": "object",
                "properties": {
                    "constraint_id": {"type": "string", "description": "The constraint to remove."},
                },
                "required": ["constraint_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_world",
            "description": "Query WorldModel for game state information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["my_actors", "enemy_actors", "enemy_bases", "economy_status", "map_control", "threat_assessment"],
                        "description": "Type of world query.",
                    },
                    "params": {"type": "object", "description": "Query-specific parameters."},
                },
                "required": ["query_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_planner",
            "description": "Query a Planner Expert for tactical suggestions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "planner_type": {
                        "type": "string",
                        "enum": ["ReconRoutePlanner", "AttackRoutePlanner", "ProductionAdvisor"],
                        "description": "Planner to query.",
                    },
                    "params": {"type": "object", "description": "Planner-specific parameters."},
                },
                "required": ["planner_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_tasks",
            "description": "Cancel other tasks matching the given filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {"type": "object", "description": "Filters: task_ids, kind, priority_below, etc."},
                },
                "required": ["filters"],
            },
        },
    },
]


def get_tool_names() -> list[str]:
    """Return all tool names."""
    return [t["function"]["name"] for t in TOOL_DEFINITIONS]


@dataclass
class ToolResult:
    """Result of a tool execution."""

    tool_call_id: str
    name: str
    result: dict[str, Any]
    duration_ms: float = 0.0
    error: Optional[str] = None


class ToolExecutor:
    """Dispatches tool calls to registered handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {}
        self._slog = get_logger("task_agent")

    def register(self, tool_name: str, handler: ToolHandler) -> None:
        self._handlers[tool_name] = handler

    def register_all(self, handlers: dict[str, ToolHandler]) -> None:
        self._handlers.update(handlers)

    async def execute(self, tool_call_id: str, name: str, arguments_json: str) -> ToolResult:
        """Execute a tool call and return the result."""
        from benchmark import span

        handler = self._handlers.get(name)
        if handler is None:
            self._slog.warn(
                "Tool call failed: handler missing",
                event="tool_handler_missing",
                tool=name,
                tool_call_id=tool_call_id,
            )
            return ToolResult(
                tool_call_id=tool_call_id,
                name=name,
                result={},
                error=f"No handler registered for tool: {name}",
            )

        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as e:
            self._slog.warn(
                "Tool call failed: invalid JSON arguments",
                event="tool_decode_failed",
                tool=name,
                tool_call_id=tool_call_id,
                error=str(e),
            )
            return ToolResult(
                tool_call_id=tool_call_id,
                name=name,
                result={},
                error=f"Invalid JSON arguments: {e}",
            )

        with span("tool_exec", name=f"tool:{name}", metadata={"tool": name}) as timer:
            try:
                self._slog.info(
                    "Executing tool call",
                    event="tool_execute",
                    tool=name,
                    tool_call_id=tool_call_id,
                    args=args,
                )
                result = await handler(name, args)
            except Exception as e:
                self._slog.error(
                    "Tool call raised exception",
                    event="tool_execute_failed",
                    tool=name,
                    tool_call_id=tool_call_id,
                    error=str(e),
                )
                return ToolResult(
                    tool_call_id=tool_call_id,
                    name=name,
                    result={},
                    duration_ms=timer.record.duration_ms if timer.record else 0.0,
                    error=str(e),
                )

        self._slog.info(
            "Tool call completed",
            event="tool_execute_completed",
            tool=name,
            tool_call_id=tool_call_id,
            duration_ms=timer.record.duration_ms if timer.record else 0.0,
            result=result,
        )
        return ToolResult(
            tool_call_id=tool_call_id,
            name=name,
            result=result,
            duration_ms=timer.record.duration_ms if timer.record else 0.0,
        )
