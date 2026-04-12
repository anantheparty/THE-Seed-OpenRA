"""Tests for Capability Task architecture (Phase 4).

Covers:
  1. Tool filtering — normal vs capability agents
  2. Context rendering — capability-specific blocks
  3. Kernel unit request mechanism (idle matching, request registration)
  4. Adjutant economy routing to Capability
  5. is_capability protection from override
"""

from __future__ import annotations

import pytest
import asyncio
import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Optional

from models import Task, TaskKind, TaskStatus
from task_agent.tools import TOOL_DEFINITIONS, CAPABILITY_TOOL_NAMES
from task_agent.agent import _NORMAL_TOOLS, _CAPABILITY_TOOLS, CAPABILITY_SYSTEM_PROMPT
from task_agent.context import (
    ContextPacket,
    WorldSummary,
    build_context_packet,
    context_to_message,
    _build_unfulfilled_requests,
    _build_unit_reservations,
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
    assert "set_rally_point" not in tool_names
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
    assert "set_rally_point" in cap_tool_names
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


def test_capability_context_has_base_progression_hint():
    """Capability context should expose the shared demo base progression hint."""
    rf = {
        "has_construction_yard": True,
        "mcv_count": 0,
        "power_plant_count": 1,
        "refinery_count": 0,
        "barracks_count": 0,
        "war_factory_count": 0,
        "buildable": {"Building": ["proc", "barr"]},
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[基地推进]" in msg["content"]
    assert "下一步：矿场" in msg["content"]
    assert "next=proc" in msg["content"]
    assert "可直接推进" in msg["content"]


def test_capability_context_prefers_runtime_base_progression_when_present():
    rf = {
        "has_construction_yard": True,
        "mcv_count": 0,
        "power_plant_count": 1,
        "refinery_count": 0,
        "barracks_count": 0,
        "war_factory_count": 0,
        "buildable": {"Building": ["proc", "barr"]},
        "base_progression": {
            "phase": "bootstrap_economy",
            "status": "共享真值：先补矿场",
            "next_unit_type": "proc",
            "next_queue_type": "Building",
            "buildable_now": True,
        },
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[基地推进]" in msg["content"]
    assert "共享真值：先补矿场" in msg["content"]
    assert "next=proc" in msg["content"]
    assert "可直接推进" in msg["content"]


def test_capability_base_progression_does_not_claim_direct_progress_when_blocked():
    rf = {
        "has_construction_yard": True,
        "mcv_count": 0,
        "power_plant_count": 1,
        "refinery_count": 0,
        "barracks_count": 0,
        "war_factory_count": 0,
        "buildable": {"Building": ["proc", "barr"]},
        "buildable_now": {"Building": []},
        "buildable_blocked": {
            "Building": [
                {"unit_type": "proc", "queue_type": "Building", "reason": "low_power"},
            ]
        },
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[基地推进]" in msg["content"]
    assert "next=proc" in msg["content"]
    assert "当前受阻:低电" in msg["content"]
    assert "可直接推进" not in msg["content"]


def test_capability_context_corrects_blocked_runtime_base_progression():
    rf = {
        "has_construction_yard": True,
        "mcv_count": 0,
        "power_plant_count": 1,
        "refinery_count": 0,
        "barracks_count": 0,
        "war_factory_count": 0,
        "buildable": {"Building": ["proc", "barr"]},
        "buildable_now": {"Building": []},
        "buildable_blocked": {
            "Building": [
                {"unit_type": "proc", "queue_type": "Building", "reason": "low_power"},
            ]
        },
        "base_progression": {
            "phase": "bootstrap_economy",
            "status": "下一步：矿场",
            "next_unit_type": "proc",
            "next_queue_type": "Building",
            "buildable_now": True,
        },
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[基地推进]" in msg["content"]
    assert "当前受阻:低电" in msg["content"]
    assert "next=proc" in msg["content"]
    assert "可直接推进" not in msg["content"]


def test_capability_header_runtime_facts_normalize_base_progression() -> None:
    rf = {
        "has_construction_yard": True,
        "mcv_count": 0,
        "power_plant_count": 1,
        "refinery_count": 0,
        "barracks_count": 0,
        "war_factory_count": 0,
        "buildable": {"Building": ["proc", "barr"]},
        "buildable_now": {"Building": []},
        "buildable_blocked": {
            "Building": [
                {"unit_type": "proc", "queue_type": "Building", "reason": "low_power"},
            ]
        },
        "base_progression": {
            "phase": "bootstrap_economy",
            "status": "下一步：矿场",
            "next_unit_type": "proc",
            "next_queue_type": "Building",
            "buildable_now": True,
        },
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    header_json = msg["content"].split("\n", 2)[1]
    header = json.loads(header_json)
    progression = header["context_packet"]["runtime_facts"]["base_progression"]
    assert progression["status"] == "当前受阻：矿场（低电）"
    assert progression["blocking_reason"] == "low_power"
    assert progression["buildable_now"] is False


def test_capability_context_distinguishes_issue_now_from_prereq_truth():
    rf = {
        "buildable": {"Building": ["powr", "proc", "barr"]},
        "buildable_now": {"Building": ["powr"]},
        "buildable_blocked": {
            "Building": [
                {"unit_type": "proc", "queue_type": "Building", "reason": "low_power"},
                {
                    "unit_type": "barr",
                    "queue_type": "Building",
                    "reason": "queue_blocked",
                    "queue_blocked_reason": "ready_not_placed",
                    "queue_blocked_items": [
                        {"queue_type": "Building", "unit_type": "powr", "display_name": "电厂"},
                    ],
                },
            ]
        },
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[可立即下单]" in msg["content"]
    assert "powr(电厂)" in msg["content"]
    assert "[前置已满足但当前受阻]" in msg["content"]
    assert "proc(矿场)=低电" in msg["content"]
    assert "barr(兵营)=队列有已完成未放置条目" in msg["content"]
    assert "成品:发电厂" in msg["content"]
    assert "[前置已满足]" in msg["content"]


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


def test_capability_context_surfaces_request_queue_block_detail():
    rf = {
        "unfulfilled_requests": [
            {
                "request_id": "r003",
                "task_label": "005",
                "category": "vehicle",
                "unit_type": "ftrk",
                "queue_type": "Vehicle",
                "count": 1,
                "fulfilled": 0,
                "urgency": "medium",
                "hint": "防空车",
                "reason": "queue_blocked",
                "queue_blocked_reason": "ready_not_placed",
                "queue_blocked_queue_types": ["Building"],
                "queue_blocked_items": [
                    {"queue_type": "Building", "unit_type": "powr", "display_name": "发电厂"},
                ],
            }
        ]
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "队列:Building" in msg["content"]
    assert "细因:队列有已完成未放置条目" in msg["content"]
    assert "成品:发电厂" in msg["content"]


def test_capability_context_translates_request_reason_labels():
    """Capability context should render human-readable request lifecycle reasons."""
    rf = {
        "unfulfilled_requests": [
            {
                "request_id": "r002",
                "task_label": "004",
                "category": "vehicle",
                "unit_type": "3tnk",
                "queue_type": "Vehicle",
                "count": 3,
                "fulfilled": 2,
                "urgency": "high",
                "hint": "重坦",
                "blocking": True,
                "min_start_package": 2,
                "reason": "start_package_released",
            }
        ]
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "已达到启动包，剩余补强中" in msg["content"]
    assert "=> 3tnk/Vehicle" in msg["content"]


def test_capability_context_has_reservations_block():
    """Capability context should show active reservations."""
    rf = {
        "unit_reservations": [
            {
                "reservation_id": "res_001",
                "task_label": "003",
                "unit_type": "e1",
                "count": 3,
                "assigned_actor_ids": [101],
                "status": "partial",
                "bootstrap_job_id": "j_boot",
                "bootstrap_task_id": "t_cap",
                "blocking": False,
                "min_start_package": 2,
            }
        ]
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[预留]" in msg["content"]
    assert "res_001" in msg["content"]
    assert "remaining=2" in msg["content"]
    assert "bootstrap=j_boot" in msg["content"]
    assert "owner=t_cap" in msg["content"]
    assert "reinforcement" in msg["content"]
    assert "start>=2" in msg["content"]


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


def test_capability_context_filters_non_demo_queue_noise():
    """Capability context should hide non-demo queue/ready-item noise."""
    rf = {
        "production_queues": {
            "Vehicle": [
                {"unit_type": "重坦", "count": 1, "source": "Kernel fast-path"},
                {"unit_type": "吉普车", "count": 1, "source": "Kernel fast-path"},
            ],
            "Aircraft": [
                {"unit_type": "米格战机", "count": 1, "source": "Capability"},
                {"unit_type": "长弓武装直升机", "count": 1, "source": "Capability"},
            ],
        },
        "ready_queue_items": [
            {"queue_type": "Vehicle", "unit_type": "重坦", "display_name": "重型坦克", "owner_actor_id": 30},
            {"queue_type": "Infantry", "unit_type": "工程师", "display_name": "工程师", "owner_actor_id": 31},
        ],
        "unit_reservations": [
            {"reservation_id": "res_ok", "request_id": "req_ok", "task_label": "003", "unit_type": "重坦", "count": 1},
            {"reservation_id": "res_bad", "request_id": "req_bad", "task_label": "004", "unit_type": "工程师", "count": 1},
        ],
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "3tnk" in msg["content"]
    assert "mig" in msg["content"]
    assert "吉普车" not in msg["content"]
    assert "长弓武装直升机" not in msg["content"]
    assert "工程师" not in msg["content"]
    assert "res_bad" not in msg["content"]


def test_capability_context_has_concise_reservations():
    """Capability context should show concise future-unit reservations."""
    rf = {
        "unit_reservations": [
            {
                "reservation_id": "res_a1",
                "request_id": "req_a1",
                "task_id": "t1",
                "task_label": "003",
                "task_summary": "发展科技",
                "unit_type": "3tnk",
                "count": 2,
                "assigned_actor_ids": [11],
                "produced_actor_ids": [21],
                "bootstrap_job_id": "j_boot",
                "status": "partial",
            }
        ]
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[预留]" in msg["content"]
    assert "res_a1" in msg["content"]
    assert "REQ-req_a1" in msg["content"]
    assert "3tnk" in msg["content"]
    assert "Vehicle" in msg["content"]
    assert "remaining=0" in msg["content"]
    assert "assigned=1" in msg["content"]
    assert "produced=1" in msg["content"]
    assert "bootstrap=j_boot" in msg["content"]


def test_capability_context_has_buildable():
    """Capability context should include prerequisite-satisfied units."""
    rf = {
        "buildable": {
            "Building": ["powr", "barr", "proc", "stek", "afld", "kenn", "silo"],
            "Infantry": ["e1", "e3", "e2", "dog"],
            "Vehicle": ["3tnk", "harv"],
            "Aircraft": ["yak", "heli"],
        }
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[前置已满足]" in msg["content"]
    assert "powr" in msg["content"]
    assert "stek" in msg["content"]
    assert "afld" in msg["content"]
    assert "3tnk" in msg["content"]
    assert "yak" in msg["content"]
    assert "kenn" not in msg["content"]
    assert "silo" not in msg["content"]
    assert "e2" not in msg["content"]
    assert "dog" not in msg["content"]
    assert "heli" not in msg["content"]


def test_capability_context_surfaces_world_sync_staleness():
    """Capability context should make stale-world status explicit."""
    rf = {
        "world_sync_stale": True,
        "world_sync_consecutive_failures": 3,
        "world_sync_total_failures": 7,
        "world_sync_last_error": "actors refresh failed",
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[世界同步]" in msg["content"]
    assert "stale=true" in msg["content"]
    assert "failures=3/7" in msg["content"]
    assert "actors refresh failed" in msg["content"]


def test_capability_context_header_includes_runtime_facts():
    """Capability JSON header should carry runtime_facts for debugging and grounding."""
    rf = {
        "has_construction_yard": True,
        "mcv_count": 0,
        "buildable": {"Building": ["powr", "kenn"]},
        "unfulfilled_requests": [{"request_id": "r1", "task_label": "003", "category": "infantry", "count": 1, "fulfilled": 0}],
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    header_json = msg["content"].split("\n", 2)[1]
    header = json.loads(header_json)
    rf_out = header["context_packet"]["runtime_facts"]
    assert rf_out["has_construction_yard"] is True
    assert rf_out["mcv_count"] == 0
    assert rf_out["buildable"]["Building"] == ["powr"]


def test_capability_context_has_base_state_and_recent_signals():
    """Capability context should expose base state and recent failed/blocked signals."""
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[
            {"kind": "task_complete", "summary": "Job failed: unsupported", "result": "failed", "data": {"unit_type": "proc"}},
            {"kind": "blocked", "summary": "缺少前置建筑", "data": {"unit_type": "weap"}},
        ],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "has_construction_yard": True,
            "mcv_count": 0,
            "power_plant_count": 1,
            "refinery_count": 0,
            "barracks_count": 1,
            "war_factory_count": 0,
            "radar_count": 0,
            "repair_facility_count": 0,
            "harvester_count": 0,
            "disabled_structure_count": 2,
            "low_power_disabled_structure_count": 1,
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[基地状态]" in msg["content"]
    assert "建造厂=有" in msg["content"]
    assert "离线建筑=2" in msg["content"]
    assert "低电离线=1" in msg["content"]
    assert "[最近信号]" in msg["content"]
    assert "proc" in msg["content"]
    assert "weap" in msg["content"]


def test_capability_header_world_summary_filters_off_roster_queue_blocked_items() -> None:
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={
            "economy": {
                "cash": 5000,
                "power_provided": 100,
                "power_drained": 20,
                "queue_blocked": True,
                "queue_blocked_items": [
                    {"queue_type": "Infantry", "unit_type": "e6", "display_name": "工程师", "owner_actor_id": 7},
                    {"queue_type": "Building", "unit_type": "powr", "display_name": "发电厂", "owner_actor_id": 1},
                ],
            },
            "military": {},
            "map": {},
            "known_enemy": {},
        },
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={},
    )
    msg = context_to_message(packet, is_capability=True)
    header_json = msg["content"].split("\n", 2)[1]
    header = json.loads(header_json)
    blocked_items = header["context_packet"]["world_summary"]["economy"]["queue_blocked_items"]
    assert len(blocked_items) == 1
    assert blocked_items[0]["unit_type"] == "powr"
    assert blocked_items[0]["display_name"] == "发电厂"


def test_capability_header_recent_signals_are_compact() -> None:
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[
            {
                "kind": "blocked",
                "job_id": f"j{i}",
                "summary": f"signal-{i}",
                "result": "failed" if i % 2 == 0 else None,
                "data": {"unit_type": "e6", "owner_actor_id": i},
            }
            for i in range(8)
        ],
        recent_events=[],
        open_decisions=[],
        runtime_facts={},
    )
    msg = context_to_message(packet, is_capability=True)
    header_json = msg["content"].split("\n", 2)[1]
    header = json.loads(header_json)
    signals = header["context_packet"]["recent_signals"]
    assert len(signals) == 6
    assert signals[0]["job_id"] == "j2"
    assert signals[-1]["job_id"] == "j7"
    assert all("data" not in signal for signal in signals)
    assert all(set(signal).issubset({"kind", "job_id", "summary", "result"}) for signal in signals)


def test_capability_header_recent_events_are_compact() -> None:
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[
            {
                "type": "PLAYER_MESSAGE",
                "timestamp": 1000 + i,
                "data": {"text": f"msg-{i}", "raw_payload": {"unexpected": i}},
            }
            for i in range(4)
        ]
        + [
            {
                "type": "LOW_POWER",
                "timestamp": 2000,
                "data": {"power_provided": 50, "power_drained": 120, "unexpected": "x"},
            },
            {
                "type": "ENEMY_DISCOVERED",
                "timestamp": 3000,
                "data": {"actor_id": 999, "position": [1, 2]},
            },
        ],
        open_decisions=[],
        runtime_facts={},
    )
    msg = context_to_message(packet, is_capability=True)
    header_json = msg["content"].split("\n", 2)[1]
    header = json.loads(header_json)
    events = header["context_packet"]["recent_events"]
    assert len(events) == 6
    assert events[0]["type"] == "PLAYER_MESSAGE"
    assert events[-1]["type"] == "ENEMY_DISCOVERED"
    assert events[0]["data"] == {"text": "msg-0"}
    assert events[4]["data"] == {"power_provided": 50, "power_drained": 120}
    assert "data" not in events[-1]


def test_capability_context_marks_deploy_mcv_as_action_not_production():
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 0, "power_drained": 0}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={"has_construction_yard": False, "mcv_count": 1},
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[基地推进]" in msg["content"]
    assert "基地车待展开" in msg["content"]
    assert "action=deploy_mcv" in msg["content"]
    assert "query_world(my_actors, category=mcv)" in msg["content"]
    assert "next=fact" not in msg["content"]


def test_capability_base_state_surfaces_disabled_structure_preview():
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "has_construction_yard": True,
            "mcv_count": 0,
            "power_plant_count": 1,
            "refinery_count": 1,
            "barracks_count": 0,
            "war_factory_count": 1,
            "radar_count": 1,
            "repair_facility_count": 0,
            "airfield_count": 0,
            "tech_center_count": 0,
            "harvester_count": 1,
            "disabled_structure_count": 2,
            "low_power_disabled_structure_count": 1,
            "disabled_structures": ["战车工厂(powerdown)", "雷达站(lowpower)"],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "离线明细=战车工厂(powerdown)、雷达站(lowpower)" in msg["content"]


def test_capability_context_surfaces_queue_block_reason():
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 20}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "queue_blocked": True,
            "queue_blocked_reason": "paused",
            "queue_blocked_queue_types": ["Building"],
            "production_queues": {"Building": [{"unit_type": "powr", "count": 1}]},
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[队列阻塞]" in msg["content"]
    assert "队列被暂停" in msg["content"]
    assert "queues=Building" in msg["content"]


def test_capability_context_filters_off_roster_queue_blocked_items() -> None:
    rf = {
        "queue_blocked": True,
        "queue_blocked_reason": "ready_not_placed",
        "queue_blocked_queue_types": ["Infantry"],
        "queue_blocked_items": [
            {"queue_type": "Infantry", "unit_type": "e6", "display_name": "工程师", "owner_actor_id": 7},
        ],
        "buildable_blocked": {
            "Building": [
                {
                    "unit_type": "powr",
                    "queue_type": "Building",
                    "reason": "queue_blocked",
                    "queue_blocked_reason": "ready_not_placed",
                    "queue_blocked_items": [
                        {"queue_type": "Infantry", "unit_type": "e6", "display_name": "工程师", "owner_actor_id": 7},
                    ],
                },
            ]
        },
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[队列阻塞] 有已完成未放置条目" in msg["content"]
    assert "工程师" not in msg["content"]
    assert "items=" not in msg["content"]
    assert "成品:" not in msg["content"]


def test_capability_context_suppresses_low_power_nudge_when_ready_power_exists() -> None:
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 140, "low_power": True}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "queue_blocked": True,
            "queue_blocked_reason": "ready_not_placed",
            "queue_blocked_queue_types": ["Building"],
            "queue_blocked_items": [
                {"queue_type": "Building", "unit_type": "powr", "display_name": "发电厂", "owner_actor_id": 1},
            ],
            "ready_queue_items": [
                {"queue_type": "Building", "unit_type": "powr", "display_name": "发电厂", "owner_actor_id": 1},
            ],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "⚡低电力" not in msg["content"]
    assert "[待处理已就绪条目]" in msg["content"]
    assert "Building: 发电厂 owner=1" in msg["content"]
    assert "[队列阻塞] 有已完成未放置条目" in msg["content"]


def test_capability_context_surfaces_truth_guard_for_unsupported_faction_roster() -> None:
    rf = {
        "faction": "allied",
        "capability_truth_blocker": "faction_roster_unsupported",
        "buildable": {"Building": ["powr", "proc", "barr"]},
        "buildable_now": {"Building": ["powr"]},
        "buildable_blocked": {"Building": [{"unit_type": "proc", "queue_type": "Building", "reason": "low_power"}]},
        "base_progression": {"status": "下一步：矿场", "next_unit_type": "proc"},
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    assert "[能力真值] faction=allied 当前 demo capability roster 未覆盖该阵营" in msg["content"]
    assert "[可立即下单]" not in msg["content"]
    assert "[前置已满足但当前受阻]" not in msg["content"]
    assert "[基地推进]" not in msg["content"]


def test_capability_context_has_runtime_status_and_parallel_tasks():
    packet = ContextPacket(
        task={"task_id": "t_test", "raw_text": "能力", "kind": "managed", "priority": 50, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "capability_status": {
                "active_job_count": 2,
                "active_job_types": ["EconomyExpert", "EconomyExpert"],
                "pending_request_count": 3,
                "blocking_request_count": 2,
                "dispatch_request_count": 1,
                "reinforcement_request_count": 1,
            }
        },
        other_active_tasks=[
            {"label": "001", "raw_text": "建造电厂", "status": "running"},
            {"label": "002", "raw_text": "探索地图", "status": "running"},
        ],
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[能力态势]" in msg["content"]
    assert "jobs=EconomyExpertx2" in msg["content"]
    assert "pending=3" in msg["content"]
    assert "blocking=2" in msg["content"]
    assert "dispatch=1" in msg["content"]
    assert "parallel=001:建造电厂(running); 002:探索地图(running)" in msg["content"]


def test_capability_context_renders_task_phase_and_blocker():
    """Capability context should render kernel-derived phase/blocker hints."""
    packet = ContextPacket(
        task={"task_id": "t_cap", "raw_text": "经济能力", "kind": "managed", "priority": 80, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "capability_status": {
                "phase": "dispatch",
                "blocker": "pending_requests_waiting_dispatch",
                "blocking_request_count": 2,
            },
            "unfulfilled_requests": [],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[阶段]" in msg["content"]
    assert "task=dispatch" in msg["content"]
    assert "[阻塞]" in msg["content"]
    assert "blocking=2" in msg["content"]


def test_capability_context_renders_inference_and_prerequisite_blockers():
    """Capability blocker block should explain richer request blocker semantics."""
    packet = ContextPacket(
        task={"task_id": "t_cap", "raw_text": "经济能力", "kind": "managed", "priority": 80, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "capability_status": {
                "blocker": "request_inference_pending",
                "inference_pending_count": 1,
            },
            "unfulfilled_requests": [{"request_id": "r1", "task_label": "007", "category": "aircraft", "count": 1, "fulfilled": 0, "hint": "", "reason": "inference_pending"}],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "等待 Capability 先确定具体生产目标" in msg["content"]
    assert "等待解析具体单位" in msg["content"]


def test_capability_context_renders_missing_prerequisite_details() -> None:
    packet = ContextPacket(
        task={"task_id": "t_cap", "raw_text": "经济能力", "kind": "managed", "priority": 80, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "capability_status": {
                "blocker": "missing_prerequisite",
                "prerequisite_gap_count": 1,
            },
            "unfulfilled_requests": [
                {
                    "request_id": "r2",
                    "task_label": "008",
                    "category": "vehicle",
                    "count": 1,
                    "fulfilled": 0,
                    "hint": "猛犸坦克",
                    "reason": "missing_prerequisite",
                    "prerequisites": ["fix", "stek", "weap"],
                }
            ],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "需先补链后再分发" in msg["content"]
    assert "前置:维修厂 + 科技中心 + 战车工厂" in msg["content"]


def test_capability_context_renders_producer_disabled_blocker() -> None:
    packet = ContextPacket(
        task={"task_id": "t_cap", "raw_text": "经济能力", "kind": "managed", "priority": 80, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "capability_status": {
                "blocker": "producer_disabled",
                "producer_disabled_count": 1,
            },
            "unfulfilled_requests": [
                {
                    "request_id": "r3",
                    "task_label": "009",
                    "category": "vehicle",
                    "count": 1,
                    "fulfilled": 0,
                    "hint": "重坦",
                    "reason": "producer_disabled",
                    "disabled_producers": ["战车工厂(powerdown)"],
                }
            ],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "对应生产建筑离线/停用" in msg["content"]
    assert "生产建筑离线/停用，需先恢复" in msg["content"]
    assert "生产点:战车工厂(powerdown)" in msg["content"]


def test_capability_context_renders_disabled_prerequisite_blocker() -> None:
    packet = ContextPacket(
        task={"task_id": "t_cap", "raw_text": "发展科技", "kind": "managed", "priority": 80, "status": "running", "created_at": time.time(), "timestamp": time.time()},
        jobs=[],
        world_summary={"economy": {"cash": 5000, "power_provided": 100, "power_drained": 40}, "military": {}, "map": {}, "known_enemy": {}},
        recent_signals=[],
        recent_events=[],
        open_decisions=[],
        runtime_facts={
            "capability_status": {
                "blocker": "disabled_prerequisite",
                "disabled_prerequisite_count": 1,
            },
            "buildable_blocked": {
                "Building": [
                    {
                        "unit_type": "stek",
                        "queue_type": "Building",
                        "reason": "disabled_prerequisite",
                        "disabled_prerequisites": ["雷达站(powerdown)"],
                    }
                ]
            },
            "unfulfilled_requests": [
                {
                    "request_id": "r4",
                    "task_label": "010",
                    "category": "building",
                    "count": 1,
                    "fulfilled": 0,
                    "hint": "科技中心",
                    "reason": "disabled_prerequisite",
                    "disabled_prerequisites": ["雷达站(powerdown)"],
                }
            ],
        },
    )
    msg = context_to_message(packet, is_capability=True)
    assert "存在离线/停用的前置建筑" in msg["content"]
    assert "前置离线:雷达站(powerdown)" in msg["content"]
    assert "stek(科技中心)=前置建筑离线" in msg["content"]


def test_capability_prompt_pins_demo_roster_and_stage_policy():
    """Capability prompt should pin demo-safe units/buildings and broad-command policy."""
    assert "powr=电厂（前置: 建造厂）" in CAPABILITY_SYSTEM_PROMPT
    assert "weap=战车工厂（前置: 矿场 + 建造厂）" in CAPABILITY_SYSTEM_PROMPT
    assert "afld=空军基地（前置: 雷达站 + 建造厂）" in CAPABILITY_SYSTEM_PROMPT
    assert "stek=科技中心（前置: 战车工厂 + 雷达站 + 建造厂）" in CAPABILITY_SYSTEM_PROMPT
    assert "e1=步兵（前置: 兵营）" in CAPABILITY_SYSTEM_PROMPT
    assert "ftrk=防空履带车（前置: 战车工厂）" in CAPABILITY_SYSTEM_PROMPT
    assert "不在上述 roster 内的单位/建筑" in CAPABILITY_SYSTEM_PROMPT
    assert "最小里程碑" in CAPABILITY_SYSTEM_PROMPT
    assert "[可立即下单]" in CAPABILITY_SYSTEM_PROMPT
    assert "[前置已满足但当前受阻]" in CAPABILITY_SYSTEM_PROMPT
    assert "[前置已满足]" in CAPABILITY_SYSTEM_PROMPT
    assert "[世界同步]" in CAPABILITY_SYSTEM_PROMPT
    assert "不要尝试 produce_units(\"fact\")" in CAPABILITY_SYSTEM_PROMPT


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


def test_capability_context_has_recent_directive_memory():
    """Capability context should render recent directives from runtime capability state."""
    packet = _make_context_packet(
        runtime_facts={
            "capability_status": {
                "recent_directives": ["发展经济", "优先补电", "补矿车"],
            }
        }
    )
    msg = context_to_message(packet, is_capability=True)
    assert "[能力近期指令]" in msg["content"]
    assert "发展经济" in msg["content"]
    assert "补矿车" in msg["content"]


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


def test_build_unfulfilled_requests_surfaces_world_sync_detail():
    result = _build_unfulfilled_requests(
        {
            "unfulfilled_requests": [
                {
                    "request_id": "r1",
                    "task_label": "003",
                    "category": "vehicle",
                    "count": 1,
                    "fulfilled": 0,
                    "hint": "重坦",
                    "blocking": True,
                    "reason": "world_sync_stale",
                    "world_sync_consecutive_failures": 4,
                    "world_sync_failure_threshold": 3,
                    "world_sync_last_error": "actors:COMMAND_EXECUTION_ERROR",
                }
            ]
        }
    )

    assert "世界状态陈旧，暂停下单" in result
    assert "同步失败:4/3" in result
    assert "最近错误:actors:COMMAND_EXECUTION_ERROR" in result


def test_build_active_production_empty():
    assert _build_active_production({}) == ""


def test_build_unit_reservations_empty():
    assert _build_unit_reservations({}) == ""
    assert _build_unit_reservations({"unit_reservations": []}) == ""


def test_build_unit_reservations_surfaces_world_sync_detail():
    result = _build_unit_reservations(
        {
            "unit_reservations": [
                {
                    "reservation_id": "res_1",
                    "request_id": "req_1",
                    "task_label": "003",
                    "unit_type": "3tnk",
                    "remaining_count": 1,
                    "assigned_actor_ids": [],
                    "produced_actor_ids": [],
                    "status": "pending",
                    "blocking": True,
                    "reason": "world_sync_stale",
                    "world_sync_consecutive_failures": 5,
                    "world_sync_failure_threshold": 3,
                    "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
                }
            ]
        }
    )

    assert "sync_fail=5/3" in result
    assert "sync_error=economy:COMMAND_EXECUTION_ERROR" in result


def test_capability_context_preserves_world_sync_detail_in_compacted_reservations():
    rf = {
        "unit_reservations": [
            {
                "reservation_id": "res_sync",
                "request_id": "req_sync",
                "task_label": "003",
                "unit_type": "3tnk",
                "count": 1,
                "remaining_count": 1,
                "assigned_actor_ids": [],
                "produced_actor_ids": [],
                "status": "pending",
                "blocking": True,
                "reason": "world_sync_stale",
                "world_sync_consecutive_failures": 5,
                "world_sync_failure_threshold": 3,
                "world_sync_last_error": "economy:COMMAND_EXECUTION_ERROR",
            }
        ]
    }
    packet = _make_context_packet(runtime_facts=rf)
    msg = context_to_message(packet, is_capability=True)
    lines = msg["content"].splitlines()
    header = json.loads(lines[1])["context_packet"]["runtime_facts"]["unit_reservations"][0]

    assert header["reason"] == "world_sync_stale"
    assert header["world_sync_consecutive_failures"] == 5
    assert header["world_sync_failure_threshold"] == 3
    assert header["world_sync_last_error"] == "economy:COMMAND_EXECUTION_ERROR"
    assert "sync_fail=5/3" in msg["content"]
    assert "sync_error=economy:COMMAND_EXECUTION_ERROR" in msg["content"]


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

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
