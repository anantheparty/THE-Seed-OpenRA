#!/usr/bin/env python3
"""Test the full THE SEED system with console bridge."""
from __future__ import annotations

import time
from adapter.openra_env import OpenRAEnv
from openra_api.game_api import GameAPI
from openra_api.models import Location, TargetsQueryParam
from openra_api.rts_middle_layer import RTSMiddleLayer

from the_seed.core.factory import NodeFactory
from the_seed.core.fsm import FSM, FSMContext, FSMState
from the_seed.utils import LogManager, build_def_style_prompt, DashboardBridge, hook_fsm_transition

logger = LogManager.get_logger()

hook_fsm_transition(FSM)


def run_fsm_once(fsm: FSM, factory: NodeFactory) -> None:
    """Execute one FSM cycle."""
    node = factory.get_node(fsm.state)
    bb = fsm.ctx.blackboard
    env = OpenRAEnv(bb.gameapi)
    bb.game_basic_state = str(env.observe())
    logger.info("Game Basic State: %s", bb.game_basic_state)

    # Broadcast FSM state update
    DashboardBridge().update_fsm_state(fsm)

    out = node.run(fsm)
    fsm.transition(out.next_state)


def main() -> None:
    """Main test function."""
    print("="*60)
    print("🧪 THE SEED Full System Test")
    print("="*60)

    # Start Console Bridge
    print("\n1️⃣  Starting Console WebSocket Server...")
    bridge = DashboardBridge()
    bridge.start(port=8090)
    time.sleep(1)
    print("   ✓ Console server running on ws://127.0.0.1:8090")

    # Connect to OpenRA
    print("\n2️⃣  Connecting to OpenRA Game API...")
    api = GameAPI(host="localhost", port=7445, language="zh")
    mid = RTSMiddleLayer(api)
    print("   ✓ Connected to OpenRA on localhost:7445")

    # Initialize FSM
    print("\n3️⃣  Initializing FSM...")
    factory = NodeFactory()
    ctx = FSMContext(goal="")
    fsm = FSM(ctx=ctx)
    bb = fsm.ctx.blackboard
    bb.gameapi = api
    bb.midapi = mid.skills
    bb.gameapi_rules = build_def_style_prompt(
        bb.midapi,
        [
            "produce_wait",
            "ensure_can_produce_unit",
            "deploy_mcv_and_wait",
            "harvester_mine",
            "dispatch_explore",
            "dispatch_attack",
            "form_group",
            "select_units",
            "query_actor",
            "unit_attribute_query",
            "query_production_queue",
            "place_building",
            "manage_production",
        ],
        title="Available functions on OpenRA midlayer API (MacroActions):",
        include_doc_first_line=True,
        include_doc_block=False,
    )

    bb.runtime_globals = {
        "gameapi": bb.midapi,
        "api": bb.midapi,
        "raw_api": api,
        "Location": Location,
        "TargetsQueryParam": TargetsQueryParam,
    }
    print(f"   ✓ FSM initialized, state={fsm.state}")

    # Broadcast initial state
    bridge.update_fsm_state(fsm)

    # Test tasks
    test_commands = [
        "查询地图信息",
        "部署基地车",
        "建造电厂",
    ]

    print(f"\n4️⃣  Running {len(test_commands)} test commands...")
    for i, command in enumerate(test_commands, 1):
        print(f"\n   Test {i}/{len(test_commands)}: '{command}'")
        fsm.ctx.goal = command

        try:
            # Track LLM call
            bridge.track_llm_call(tokens=100)

            # Run FSM cycle
            run_fsm_once(fsm, factory)

            # Track action
            bridge.track_action(f"command_{i}", success=True)

            print(f"   ✓ Command completed, new state={fsm.state}")

            # Wait between commands
            time.sleep(2)

        except Exception as e:
            logger.error(f"Command failed: {e}")
            bridge.track_action(f"command_{i}", success=False)
            print(f"   ✗ Command failed: {e}")

    print("\n" + "="*60)
    print("✅ Test completed!")
    print("="*60)
    print("\nConsole should show:")
    print("  - FSM state transitions")
    print("  - Agent metrics (LLM calls, actions)")
    print("  - Game state updates")
    print("\nKeeping server alive for 10 seconds...")

    # Keep running for a bit to let console receive updates
    try:
        for i in range(10, 0, -1):
            print(f"  Shutting down in {i}s...", end="\r")
            time.sleep(1)
        print("\n\n👋 Shutting down...")
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted, shutting down...")


if __name__ == "__main__":
    main()
