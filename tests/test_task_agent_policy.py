"""Tests for structured TaskAgent policy helpers."""

from __future__ import annotations

import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from task_agent.policy import (
    CAPABILITY_ROSTER_TEXT,
    ORDINARY_HIDDEN_TOOL_NAMES,
    ORDINARY_ROSTER_TEXT,
    build_capability_system_prompt,
    build_system_prompt,
    capability_tools,
    ordinary_tools,
)
from task_agent.tools import CAPABILITY_TOOL_NAMES, TOOL_DEFINITIONS


def test_policy_tool_surfaces_match_existing_boundaries() -> None:
    normal = {tool["function"]["name"] for tool in ordinary_tools(TOOL_DEFINITIONS)}
    capability = {tool["function"]["name"] for tool in capability_tools(TOOL_DEFINITIONS, CAPABILITY_TOOL_NAMES)}

    all_tools = {tool["function"]["name"] for tool in TOOL_DEFINITIONS}
    assert ORDINARY_HIDDEN_TOOL_NAMES == {"produce_units", "set_rally_point"}
    assert normal == all_tools - ORDINARY_HIDDEN_TOOL_NAMES
    assert capability == CAPABILITY_TOOL_NAMES
    assert "deploy_mcv" in capability


def test_policy_prompts_pin_demo_roster_text() -> None:
    normal_prompt = build_system_prompt()
    capability_prompt = build_capability_system_prompt()

    assert "e1=步兵" in ORDINARY_ROSTER_TEXT
    assert "powr" in CAPABILITY_ROSTER_TEXT
    assert "tsla=特斯拉塔" in CAPABILITY_ROSTER_TEXT
    assert "ftur=火焰塔" in CAPABILITY_ROSTER_TEXT
    assert "sam=防空塔" in CAPABILITY_ROSTER_TEXT
    assert "e1=步兵" in normal_prompt
    assert "不能自行补生产" in normal_prompt
    assert "只在有明确需求时才行动" in capability_prompt
    assert "不在上述 roster 内的单位/建筑" in capability_prompt
    assert "`deploy_mcv`" in capability_prompt
    assert "`query_world`" in capability_prompt
    assert "`query_planner`" in capability_prompt
    assert "`set_rally_point`" in capability_prompt
    assert "`update_subscriptions`" in capability_prompt
    assert "`send_task_message`" in capability_prompt
    assert "展开基地车时可用 deploy_mcv" in capability_prompt
    assert "通常是 produce_units；展开基地车时可用 deploy_mcv" in capability_prompt
    assert "world_summary（弱参考，不单独驱动决策）" in capability_prompt


def test_request_units_schema_excludes_building_category() -> None:
    request_units = next(tool for tool in TOOL_DEFINITIONS if tool["function"]["name"] == "request_units")
    categories = request_units["function"]["parameters"]["properties"]["category"]["enum"]
    assert "building" not in categories

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
