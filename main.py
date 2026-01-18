from __future__ import annotations

from adapter.openra_env import OpenRAEnv
# from inner_loop import InnerLoopRuntime
from openra_api.game_api import GameAPI
from openra_api.models import Location, TargetsQueryParam, Actor,MapQueryResult,FrozenActor,ControlPoint,ControlPointQueryResult,MatchInfoQueryResult,PlayerBaseInfo,ScreenInfoResult
from openra_api.rts_middle_layer import RTSMiddleLayer

from the_seed.core.factory import NodeFactory
from the_seed.core.fsm import FSM, FSMContext, FSMState
from the_seed.utils import LogManager, build_def_style_prompt, DashboardBridge, hook_fsm_transition

logger = LogManager.get_logger()

hook_fsm_transition(FSM)


def run_fsm_once(fsm: FSM, factory: NodeFactory) -> None:
    node = factory.get_node(fsm.state)
    bb = fsm.ctx.blackboard
    env = OpenRAEnv(bb.gameapi)
    bb.game_basic_state = str(env.observe())
    logger.info("Game Basic State: %s", bb.game_basic_state)
    out = node.run(fsm)
    fsm.transition(out.next_state)


def main() -> None:
    api = GameAPI(host="localhost", port=7445, language="zh")
    mid = RTSMiddleLayer(api)

    factory = NodeFactory()
    # inner = InnerLoopRuntime()

    ctx = FSMContext(goal="")  # 由玩家输入驱动
    fsm = FSM(ctx=ctx)
    bb = fsm.ctx.blackboard
    # 观测侧仍使用底层 GameAPI（OpenRAEnv 需要它）
    bb.gameapi = api
    # 执行侧使用 midlayer 的 API（MacroActions），供 build_def_style_prompt 与 runtime_globals 使用
    bb.midapi = mid.skills
    bb.gameapi_rules = build_def_style_prompt(
        bb.midapi,
        [
            # "produce",
            "produce_wait",
            # "ensure_can_build_wait",
            "ensure_can_produce_unit",
            "deploy_mcv_and_wait",
            # "deploy",
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
        # LLM/执行环境默认使用 midlayer API（更安全、更一致）
        "gameapi": bb.midapi,
        "api": bb.midapi,
        # 如需访问底层 RPC，可用 raw_api
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

    logger.info("FSM start state=%s", fsm.state)

    # Define command handler
    def handle_command(command: str) -> None:
        """Handle commands from Dashboard."""
        try:
            logger.info(f"Processing command: {command}")
            fsm.ctx.goal = command

            # Run FSM until completion or waiting for user input
            while fsm.state != FSMState.NEED_USER and fsm.state != FSMState.STOP:
                run_fsm_once(fsm, factory)

                # Broadcast updated state after each transition
                DashboardBridge().update_fsm_state(fsm)

            logger.info(f"Command completed. Final state: {fsm.state}")

        except Exception as e:
            logger.error(f"Error processing command: {e}", exc_info=True)
            DashboardBridge().send_log("error", f"Command failed: {str(e)}")

    # Start Dashboard Bridge with command handler
    DashboardBridge().start(port=8080, command_handler=handle_command)

    # Broadcast initial FSM state to dashboard
    DashboardBridge().update_fsm_state(fsm)

    logger.info("✓ System ready. Waiting for commands from Dashboard...")
    logger.info("  Dashboard URL: http://localhost:8080")
    logger.info("  Press Ctrl+C to stop")

    # Keep the server running, waiting for Dashboard commands
    # Commands will be received via WebSocket and processed by DashboardBridge
    try:
        import signal
        import time

        def signal_handler(sig, frame):
            logger.info("Shutting down...")
            raise SystemExit(0)

        signal.signal(signal.SIGINT, signal_handler)

        # Keep alive loop
        while True:
            time.sleep(1)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Backend stopped.")


if __name__ == "__main__":
    main()
