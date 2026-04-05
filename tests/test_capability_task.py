"""Tests for Capability Task architecture (Phase 4).

Covers:
  1. Tool filtering — normal vs capability agents
  2. Context rendering — capability-specific blocks
  3. Kernel unit request mechanism (idle matching, request registration)
  4. Adjutant economy routing to Capability
  5. is_capability protection from override
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from models import Task, TaskKind, TaskStatus
from task_agent.tools import TOOL_DEFINITIONS, CAPABILITY_TOOL_NAMES
from task_agent.agent import _NORMAL_TOOLS, _CAPABILITY_TOOLS
from task_agent.context import (
    ContextPacket,
    WorldSummary,
    build_context_packet,
    context_to_message,
    _build_unfulfilled_requests,
    _build_active_production,
    _build_player_messages,
)
from adjutant import Adjutant, AdjutantConfig


# --- Helpers ---

def _make_task(raw_text="测试任务", is_capability=False, status="running"):
    t = Task(
        task_id="t_test",
        raw_text=raw_text,
        kind=TaskKind.MANAGED,
        priority=50,
        status=TaskStatus(status),
    )
    t.is_capability = is_capability
    return t


def _make_context_packet(task_dict=None, runtime_facts=None, events=None, world_summary=None):
    return ContextPacket(
        task=task_dict or {"task_id": "t_test", "raw_text": "测试", "kind": "managed",
                           "priority": 50, "status": "running", "created_at": time.time(),
                           "timestamp": time.time()},
        jobs=[],
        world_summary=world_summary or {"economy": {"cash": 5000, "power_provided": 100,
                                                      "power_drained": 40, "harvester_count": 2},
                                          "military": {"self_units": 10, "enemy_units": 5, "idle_self_units": 3},
                                          "map": {"explored_pct": 0.5},
                                          "known_enemy": {}},
        recent_signals=[],
        recent_events=events or [],
        open_decisions=[],
        runtime_facts=runtime_facts or {},
    )


# =====================================================================
# 1. Tool Filtering Tests
# =====================================================================

def test_normal_tools_exclude_produce_units():
    """Normal agents should not have produce_units in their tool set."""
    tool_names = {t["function"]["name"] for t in _NORMAL_TOOLS}
    assert "produce_units" not in tool_names
    assert "request_units" in tool_names
    assert "attack" in tool_names
    assert "scout_map" in tool_names


def test_capability_tools_only_capability_names():
    """Capability agents should only have CAPABILITY_TOOL_NAMES tools."""
    cap_tool_names = {t["function"]["name"] for t in _CAPABILITY_TOOLS}
    assert cap_tool_names == CAPABILITY_TOOL_NAMES


def test_capability_tools_include_produce_units():
    """Capability agents should have produce_units."""
    cap_tool_names = {t["function"]["name"] for t in _CAPABILITY_TOOLS}
    assert "produce_units" in cap_tool_names
    assert "query_world" in cap_tool_names
    assert "query_planner" in cap_tool_names


def test_capability_tools_exclude_combat():
    """Capability agents should not have combat/movement tools."""
    cap_tool_names = {t["function"]["name"] for t in _CAPABILITY_TOOLS}
    assert "attack" not in cap_tool_names
    assert "move_units" not in cap_tool_names
    assert "scout_map" not in cap_tool_names
    assert "request_units" not in cap_tool_names


# =====================================================================
# 2. Context Rendering Tests
# =====================================================================

def test_capability_context_has_economy_block():
    """Capability context should include [经济] block."""
    packet = _make_context_packet()
    msg = context_to_message(packet, is_capability=True)
    assert "[经济]" in msg["content"]
    assert "资金:5000" in msg["content"]


def test_capability_context_has_unfulfilled_requests():
    """Capability context should show unfulfilled requests."""
    rf = {
        "unfulfilled_requests": [
            {
                "request_id": "r001",
                "task_label": "003",
                "task_summary": "空袭",
                "category": "aircraft",
                "count": 2,
                "fulfilled": 0,
                "urgency": "high",
                "hint": "对地攻击机",
                "reason": "无机场",
            }
        ]
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[待处理请求]" in msg["content"]
    assert "REQ-r001" in msg["content"]
    assert "aircraft" in msg["content"]
    assert "无机场" in msg["content"]


def test_capability_context_has_active_production():
    """Capability context should show active production queues."""
    rf = {
        "production_queues": {
            "Vehicle": [{"unit_type": "3tnk", "count": 3, "source": "Kernel fast-path"}],
            "Infantry": [],
        }
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[生产队列]" in msg["content"]
    assert "3tnk" in msg["content"]
    assert "Kernel fast-path" in msg["content"]


def test_capability_context_has_buildable():
    """Capability context should include buildable units."""
    rf = {
        "buildable": {
            "Building": ["powr", "barr", "proc"],
            "Infantry": ["e1", "e3"],
            "Vehicle": ["3tnk", "harv"],
        }
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[可造]" in msg["content"]
    assert "powr" in msg["content"]
    assert "3tnk" in msg["content"]


def test_capability_context_has_player_messages():
    """Capability context should show player messages."""
    events = [
        {
            "type": "PLAYER_MESSAGE",
            "timestamp": time.time() - 5,
            "data": {"text": "多建电厂", "timestamp": time.time() - 5},
        }
    ]
    packet = _make_context_packet(events=events)
    msg = context_to_message(packet, is_capability=True)
    assert "[玩家追加指令]" in msg["content"]
    assert "多建电厂" in msg["content"]


def test_normal_context_no_economy_block():
    """Normal task context should not have capability-specific blocks."""
    packet = _make_context_packet()
    msg = context_to_message(packet, is_capability=False)
    assert "[经济]" not in msg["content"]
    assert "[待处理请求]" not in msg["content"]
    assert "[生产队列]" not in msg["content"]
    # Should have normal task blocks
    assert "[任务]" in msg["content"]
    assert "[世界]" in msg["content"]


def test_normal_context_has_player_messages():
    """Normal context should also show player messages (for merge path)."""
    events = [
        {
            "type": "PLAYER_MESSAGE",
            "timestamp": time.time() - 3,
            "data": {"text": "敌人在左边", "timestamp": time.time() - 3},
        }
    ]
    packet = _make_context_packet(events=events)
    msg = context_to_message(packet, is_capability=False)
    assert "[玩家追加指令]" in msg["content"]
    assert "敌人在左边" in msg["content"]


# =====================================================================
# 3. Context block builders
# =====================================================================

def test_build_unfulfilled_requests_empty():
    assert _build_unfulfilled_requests({}) == ""
    assert _build_unfulfilled_requests({"unfulfilled_requests": []}) == ""


def test_build_active_production_empty():
    assert _build_active_production({}) == ""


def test_build_player_messages_no_events():
    assert _build_player_messages([]) == ""


def test_build_player_messages_filters_non_player():
    events = [
        {"type": "UNIT_DIED", "data": {"actor_id": 1}},
        {"type": "PLAYER_MESSAGE", "data": {"text": "快点"}, "timestamp": time.time()},
    ]
    result = _build_player_messages(events)
    assert "快点" in result
    assert "UNIT_DIED" not in result


# =====================================================================
# 4. Adjutant Economy Routing Tests
# =====================================================================

class MockTask:
    def __init__(self, task_id, raw_text, status="running"):
        self.task_id = task_id
        self.raw_text = raw_text
        self.status = type("S", (), {"value": status})()
        self.kind = TaskKind.MANAGED
        self.priority = 50
        self.created_at = time.time()
        self.timestamp = time.time()
        self.label = ""
        self.is_capability = False


class MockKernel:
    def __init__(self):
        self.created_tasks = []
        self.started_jobs = []
        self._pending_questions = []
        self._tasks = []
        self._task_counter = 0
        self._job_counter = 0
        self.injected_messages = []

    def create_task(self, raw_text, kind, priority, info_subscriptions=None, *, skip_agent=False):
        self._task_counter += 1
        task = MockTask(f"t_{self._task_counter}", raw_text)
        task.label = f"{self._task_counter:03d}"
        self.created_tasks.append({"raw_text": raw_text, "kind": kind, "priority": priority})
        self._tasks.append(task)
        return task

    def start_job(self, task_id, expert_type, config):
        self._job_counter += 1
        return type("MockJob", (), {"job_id": f"j_{self._job_counter}"})()

    def submit_player_response(self, response, *, now=None):
        return {"ok": True, "status": "delivered"}

    def list_pending_questions(self):
        return []

    def list_tasks(self):
        return list(self._tasks)

    def cancel_task(self, task_id):
        self._tasks = [t for t in self._tasks if t.task_id != task_id]
        return True

    def is_direct_managed(self, task_id):
        return False

    def inject_player_message(self, task_id, text):
        target = next((t for t in self._tasks if t.task_id == task_id), None)
        if target is None:
            return False
        self.injected_messages.append({"task_id": task_id, "text": text})
        return True

    @property
    def capability_task_id(self):
        cap = next((t for t in self._tasks if getattr(t, "is_capability", False)), None)
        return cap.task_id if cap else None


class MockWorldModel:
    def world_summary(self):
        return {"economy": {"cash": 5000}, "timestamp": time.time()}

    def query(self, query_type, params=None):
        return {"data": [], "timestamp": time.time()}

    def refresh_health(self):
        return {"stale": False}


def _make_adjutant(kernel=None, llm_responses=None):
    from llm import MockProvider
    kernel = kernel or MockKernel()
    provider = MockProvider(llm_responses or [])
    return Adjutant(
        llm=provider,
        kernel=kernel,
        world_model=MockWorldModel(),
        config=AdjutantConfig(),
    ), kernel


def test_economy_command_merges_to_capability():
    """Economy commands should be forwarded to the EconomyCapability task."""
    adjutant, kernel = _make_adjutant()
    # Create a capability task
    cap_task = MockTask("t_cap", "经济规划")
    cap_task.label = "cap"
    cap_task.is_capability = True
    kernel._tasks.append(cap_task)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(adjutant.handle_player_input("爆兵"))
    finally:
        loop.close()

    assert result.get("ok") is True
    assert result.get("merged") is True
    assert len(kernel.injected_messages) == 1
    assert kernel.injected_messages[0]["task_id"] == "t_cap"
    assert "爆兵" in kernel.injected_messages[0]["text"]


def test_economy_command_without_capability_creates_task():
    """Economy commands without an active Capability should create a normal task."""
    llm_resp = [
        LLMResponse(
            text='{"type":"command","disposition":"new","confidence":0.9,"reason":"no cap"}',
            model="mock",
        )
    ]
    adjutant, kernel = _make_adjutant(llm_responses=llm_resp)
    # No capability task — the economy keyword check will find no cap,
    # but it should fall through to the LLM classification path
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(adjutant.handle_player_input("发展经济"))
    finally:
        loop.close()
    # Should have fallen through to creating a new task
    assert result.get("ok") is True


def test_override_blocked_for_capability():
    """is_capability tasks should not be overridden."""
    adjutant, kernel = _make_adjutant()
    cap_task = MockTask("t_cap", "经济规划")
    cap_task.label = "001"
    cap_task.is_capability = True
    kernel._tasks.append(cap_task)

    # Simulate override disposition targeting the capability task
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(adjutant._handle_override("全力进攻", "001"))
    finally:
        loop.close()
    # The capability task should still exist (not cancelled)
    assert any(t.task_id == "t_cap" for t in kernel._tasks)
    # A new task should be created instead
    assert result.get("ok") is True


def test_find_oldest_agent_task_skips_capability():
    """_find_oldest_agent_task should skip capability tasks."""
    from adjutant import AdjutantContext
    context = AdjutantContext(
        active_tasks=[
            {"label": "001", "is_nlu": False, "is_capability": True, "age_seconds": 100},
            {"label": "002", "is_nlu": False, "is_capability": False, "age_seconds": 50},
            {"label": "003", "is_nlu": True, "is_capability": False, "age_seconds": 200},
        ],
        pending_questions=[],
        recent_dialogue=[],
        player_input="test",
    )
    result = Adjutant._find_oldest_agent_task(context)
    assert result == "002"  # Should pick 002 (50s), not 001 (capability) or 003 (NLU)


def test_nlu_notify_capability_on_production():
    """NLU production commands should notify Capability."""
    adjutant, kernel = _make_adjutant()
    cap_task = MockTask("t_cap", "经济规划")
    cap_task.label = "cap"
    cap_task.is_capability = True
    kernel._tasks.append(cap_task)

    # Call _notify_capability_of_nlu directly
    adjutant._notify_capability_of_nlu("造5辆坦克", "EconomyExpert")
    assert len(kernel.injected_messages) == 1
    assert "NLU直达" in kernel.injected_messages[0]["text"]

    # Non-economy expert should not notify
    adjutant._notify_capability_of_nlu("侦察", "ReconExpert")
    assert len(kernel.injected_messages) == 1  # Still 1


# Need this import for test_economy_command_without_capability_creates_task
from llm import LLMResponse
