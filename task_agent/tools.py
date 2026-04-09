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
    # --- Expert action tools (each Expert exposed as its own tool) ---
    {
        "type": "function",
        "function": {
            "name": "deploy_mcv",
            "description": (
                "Deploy a Mobile Construction Vehicle (MCV) to build a Construction Yard. "
                "部署基地车、建造建造厂。"
                "Query query_world(my_actors) first to get the MCV's actor_id. "
                "target_position is optional — omit to deploy in place."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_id": {
                        "type": "integer",
                        "description": "Actor ID of the MCV to deploy.",
                    },
                    "target_position": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional [x, y] map position to move the MCV before deploying.",
                    },
                },
                "required": ["actor_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scout_map",
            "description": (
                "Send scouts to explore the map and locate enemy targets. "
                "探索地图、侦察敌情、找敌人基地。"
                "Uses available mobile units — ensure infantry or vehicles exist before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_region": {
                        "type": "string",
                        "enum": ["northeast", "southwest", "northwest", "southeast", "enemy_half", "full_map"],
                        "description": "Region of the map to scout.",
                    },
                    "target_type": {
                        "type": "string",
                        "enum": ["base", "army", "expansion"],
                        "description": "Type of target to look for.",
                    },
                    "target_owner": {
                        "type": "string",
                        "description": "Owner filter, default 'enemy'.",
                    },
                    "retreat_hp_pct": {
                        "type": "number",
                        "description": "HP fraction at which scouts retreat (0–1, default 0.3).",
                    },
                    "avoid_combat": {
                        "type": "boolean",
                        "description": "Whether scouts should avoid engaging enemies (default true).",
                    },
                    "scout_count": {
                        "type": "integer",
                        "description": "侦察单位数量 (default 1). 值越大探索越快但损失风险越高。",
                    },
                    "actor_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional explicit scout actor ids. When provided, recon stays on these units instead of asking for generic scouts.",
                    },
                },
                "required": ["search_region", "target_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "produce_units",
            "description": (
                "Produce units or construct buildings via the production queue. "
                "生产单位、建造建筑。"
                "unit_type accepts internal codes (e.g. 'e1', 'powr', '2tnk'), "
                "Chinese names (e.g. '步兵', '发电厂', '重坦'), or English aliases. "
                "Use queue_type='Building' for structures."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_type": {
                        "type": "string",
                        "description": "Unit or building to produce. Accepts code, Chinese name, or English alias.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of units to produce.",
                    },
                    "queue_type": {
                        "type": "string",
                        "enum": ["Infantry", "Vehicle", "Building", "Aircraft", "Defense"],
                        "description": "Production queue to use.",
                    },
                    "repeat": {
                        "type": "boolean",
                        "description": "Whether to repeat production indefinitely (default false).",
                    },
                },
                "required": ["unit_type", "count", "queue_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_units",
            "description": (
                "Request units from Kernel. Returns fulfilled (idle match) or waiting (production needed). "
                "请求单位。你不能直接生产，只能请求。Kernel 负责匹配/生产/分配。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["infantry", "vehicle", "aircraft", "building"],
                        "description": "Unit category to request.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of units to request.",
                    },
                    "urgency": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "description": "Request urgency. Higher urgency gets priority allocation.",
                    },
                    "hint": {
                        "type": "string",
                        "description": "Hint for unit type, e.g. '重坦', '步兵'. Kernel infers specific unit.",
                    },
                },
                "required": ["category", "count", "urgency", "hint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_units",
            "description": (
                "Move units to a target map position. "
                "撤退、移动部队到指定位置。"
                "actor_ids is optional — omit to move all available units for this task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_position": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "[x, y] destination on the map.",
                    },
                    "move_mode": {
                        "type": "string",
                        "enum": ["move", "attack_move", "retreat"],
                        "description": "Movement mode (default 'move').",
                    },
                    "arrival_radius": {
                        "type": "integer",
                        "description": "Radius in cells within which arrival is considered (default 5).",
                    },
                    "actor_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Specific actor IDs to move. Omit to use all task-bound units.",
                    },
                    "unit_count": {
                        "type": "integer",
                        "description": "Number of units to move (0 = all available, default 0). Only used when actor_ids is omitted.",
                    },
                },
                "required": ["target_position"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_units",
            "description": (
                "Immediately stop the current task's units. "
                "停止当前任务控制的部队，立刻中断移动/追击/攻击。"
                "actor_ids is optional — omit to stop all task-bound units."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Specific actor IDs to stop. Omit to use all task-bound units.",
                    },
                    "unit_count": {
                        "type": "integer",
                        "description": "Number of units to stop (0 = all available, default 0). Only used when actor_ids is omitted.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repair_units",
            "description": (
                "Send the current task's damaged units to repair immediately. "
                "将当前任务控制的受损单位送去维修。"
                "actor_ids is optional — omit to repair all task-bound damaged units."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "actor_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Specific actor IDs to repair. Omit to use all task-bound damaged units.",
                    },
                    "unit_count": {
                        "type": "integer",
                        "description": "Number of units to repair (0 = all available, default 0). Only used when actor_ids is omitted.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attack",
            "description": (
                "Send units to attack a target position. "
                "进攻、包围、骚扰、防守指定位置。"
                "engagement_mode controls tactics: 'assault'=full attack, 'harass'=hit-and-run, "
                "'hold'=hold position, 'surround'=encircle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_position": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "[x, y] target position to attack.",
                    },
                    "engagement_mode": {
                        "type": "string",
                        "enum": ["assault", "harass", "hold", "surround"],
                        "description": "Combat tactic (default 'assault').",
                    },
                    "max_chase_distance": {
                        "type": "integer",
                        "description": "Maximum cells to chase retreating enemies (default 20).",
                    },
                    "retreat_threshold": {
                        "type": "number",
                        "description": "HP fraction at which units retreat (0–1, default 0.3).",
                    },
                    "unit_count": {
                        "type": "integer",
                        "description": "Number of units to send. 0 or omit = all available idle combat units.",
                    },
                    "actor_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional explicit combat actor ids. When provided, only these units are controlled.",
                    },
                },
                "required": ["target_position"],
            },
        },
    },
    # --- Job management tools ---
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
    {
        "type": "function",
        "function": {
            "name": "update_subscriptions",
            "description": (
                "Modify which Info Expert data sources are included in your context each wake cycle. "
                "Use this to add or remove optional data feeds mid-task. "
                "Available keys: 'threat' (enemy threat assessment), "
                "'base_state' (base health and production status), "
                "'production' (production advisor). "
                "Example: add=['threat'] when scouting reveals enemy forces."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "add": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["threat", "base_state", "production"]},
                        "description": "Subscription keys to add.",
                    },
                    "remove": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["threat", "base_state", "production"]},
                        "description": "Subscription keys to remove.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_task_message",
            "description": (
                "Send a message to the player. Use this to communicate task progress, warnings, or ask for clarification. "
                "type='info': status update or observation. "
                "type='warning': important alert requiring player attention. "
                "type='question': ask the player to choose between options (requires options list). "
                "type='complete_report': final summary when completing or failing the task. "
                "Use 'question' when player intent is ambiguous or you need authorization for a risky action. "
                "Do NOT spam info messages — only send when the player genuinely benefits from knowing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["info", "warning", "question", "complete_report"],
                        "description": "Message type.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Message text shown to the player.",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Answer options (required for type='question').",
                    },
                    "timeout_s": {
                        "type": "number",
                        "description": "Seconds before question auto-resolves to default_option (default: 60, type='question' only).",
                    },
                    "default_option": {
                        "type": "string",
                        "description": "Option used if player does not respond within timeout_s (type='question' only, must be one of options).",
                    },
                },
                "required": ["type", "content"],
            },
        },
    },
]


def get_tool_names() -> list[str]:
    """Return all tool names."""
    return [t["function"]["name"] for t in TOOL_DEFINITIONS]


# Tools available to EconomyCapability (persistent LLM task for global economy).
CAPABILITY_TOOL_NAMES: frozenset[str] = frozenset({
    "produce_units", "query_world", "query_planner", "update_subscriptions", "send_task_message",
})


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
