#!/usr/bin/env python3
"""
Test the simplified THE SEED system.
"""
from __future__ import annotations

import time
from adapter.openra_env import OpenRAEnv
from openra_api.game_api import GameAPI
from openra_api.models import (
    Location,
    TargetsQueryParam,
    Actor,
    MapQueryResult,
    FrozenActor,
    ControlPoint,
    ControlPointQueryResult,
    MatchInfoQueryResult,
    PlayerBaseInfo,
    ScreenInfoResult,
)
from openra_api.rts_middle_layer import RTSMiddleLayer

from the_seed.core import CodeGenNode, SimpleExecutor, ExecutorContext
from the_seed.model import ModelFactory
from the_seed.config import load_config
from the_seed.utils import LogManager, build_def_style_prompt, DashboardBridge

logger = LogManager.get_logger()


def create_executor(api: GameAPI, mid: RTSMiddleLayer) -> SimpleExecutor:
    """Create the simplified executor."""
    cfg = load_config()
    
    # Create model
    model_config = cfg.model_templates.get(
        cfg.node_models.action,
        cfg.model_templates.get("default")
    )
    model = ModelFactory.build("codegen", model_config)
    
    # Create code generator
    codegen = CodeGenNode(model)
    
    # Create environment
    env = OpenRAEnv(api)
    
    # Build API documentation
    api_rules = build_def_style_prompt(
        mid.skills,
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
    
    # Runtime globals
    runtime_globals = {
        "api": mid.skills,
        "gameapi": mid.skills,
        "raw_api": api,
        "Location": Location,
        "TargetsQueryParam": TargetsQueryParam,
        "Actor": Actor,
        "MapQueryResult": MapQueryResult,
        "FrozenActor": FrozenActor,
        "ControlPoint": ControlPoint,
        "ControlPointQueryResult": ControlPointQueryResult,
        "MatchInfoQueryResult": MatchInfoQueryResult,
        "PlayerBaseInfo": PlayerBaseInfo,
        "ScreenInfoResult": ScreenInfoResult,
    }
    
    # Create context
    ctx = ExecutorContext(
        api=mid.skills,
        raw_api=api,
        api_rules=api_rules,
        runtime_globals=runtime_globals,
        observe_fn=env.observe,
    )
    
    return SimpleExecutor(codegen, ctx)


def main() -> None:
    """Main test function."""
    print("=" * 60)
    print("🧪 THE SEED Simplified System Test")
    print("=" * 60)

    # Start Dashboard Bridge (optional)
    print("\n1️⃣  Starting Dashboard WebSocket Server...")
    bridge = DashboardBridge()
    bridge.start(port=8080)
    time.sleep(1)
    print("   ✓ Dashboard server running on ws://127.0.0.1:8080")

    # Connect to OpenRA
    print("\n2️⃣  Connecting to OpenRA Game API...")
    try:
        api = GameAPI(host="localhost", port=7445, language="zh")
        mid = RTSMiddleLayer(api)
        print("   ✓ Connected to OpenRA on localhost:7445")
    except Exception as e:
        print(f"   ✗ Failed to connect to OpenRA: {e}")
        print("   Make sure OpenRA is running with the AI API enabled.")
        return

    # Initialize executor
    print("\n3️⃣  Initializing Executor...")
    try:
        executor = create_executor(api, mid)
        print("   ✓ Executor initialized")
    except Exception as e:
        print(f"   ✗ Failed to initialize executor: {e}")
        return

    # Test commands
    test_commands = [
        "查询当前游戏状态",
        "部署基地车",
        "建造一个电厂",
    ]

    print(f"\n4️⃣  Running {len(test_commands)} test commands...")
    
    for i, command in enumerate(test_commands, 1):
        print(f"\n   Test {i}/{len(test_commands)}: '{command}'")
        print("-" * 50)
        
        try:
            result = executor.run(command)
            
            if result.success:
                print(f"   ✓ Success: {result.message}")
            else:
                print(f"   ✗ Failed: {result.message}")
                if result.error:
                    print(f"   Error: {result.error}")
            
            if result.observations:
                print(f"   Observations: {result.observations}")
            
            # Log to dashboard
            bridge.send_log(
                "info" if result.success else "error",
                result.message
            )
            
        except Exception as e:
            logger.error(f"Command failed: {e}")
            print(f"   ✗ Exception: {e}")
            bridge.send_log("error", f"Exception: {str(e)}")
        
        # Wait between commands
        time.sleep(2)

    print("\n" + "=" * 60)
    print("✅ Test completed!")
    print("=" * 60)
    
    print("\nKeeping server alive for 5 seconds...")
    try:
        for i in range(5, 0, -1):
            print(f"  Shutting down in {i}s...", end="\r")
            time.sleep(1)
        print("\n\n👋 Shutting down...")
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted, shutting down...")


if __name__ == "__main__":
    main()
