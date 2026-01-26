from __future__ import annotations
import time
import signal
import sys
from typing import List

# 确保能导入 the-seed (如果是 submodule)
# sys.path.append("./the-seed") 

from openra_api.game_api import GameAPI
from openra_api.game_midlayer import RTSMiddleLayer
from openra_state.api_client import GameAPI as StateGameAPI
from openra_state.intel.intelligence_service import IntelligenceService
from the_seed.utils import LogManager, DashboardBridge

from agents.global_blackboard import GlobalBlackboard
from agents.base_agent import BaseAgent

logger = LogManager.get_logger()

def main() -> None:
    # 1. 初始化基础设施
    api = GameAPI(host="localhost", port=7445, language="zh")
    mid = RTSMiddleLayer(api)
    state_api = StateGameAPI(host="localhost", port=7445, language="zh")
    global_bb = GlobalBlackboard()
    intel_service = IntelligenceService(state_api, global_bb)

    # 2. 初始化智能体
    # Phase 1: 仅启动一个 "Adjutant" (副官)，它目前承载原来的单体逻辑
    agents: List[BaseAgent] = []
    
    adjutant = BaseAgent(
        name="Adjutant",
        global_bb=global_bb,
        game_api=api,
        mid_layer=mid
    )
    agents.append(adjutant)

    # Economy Specialist (Rule-based)
    # 使用独立的 API 连接
    api_economy = GameAPI(host="localhost", port=7445, language="zh")
    economy = EconomyAgent(
        name="Economy",
        global_bb=global_bb,
        game_api=api_economy
    )
    agents.append(economy)

    # 3. 定义 Dashboard 命令处理器
    def handle_command(command: str) -> None:
        """
        Dashboard 回调函数。
        不再直接运行 FSM，而是将指令写入 GlobalBlackboard。
        """
        logger.info(f"Dashboard command received: {command}")
        # 将指令发布到全局黑板，由 Adjutant 领取
        global_bb.command = command
        # 也可以通过 Signal 机制发布
        # global_bb.publish_signal(Signal(sender="User", receiver="Adjutant", type="command", payload=command))

    # 4. 启动 Dashboard 服务 (后台线程)
    DashboardBridge().start(port=8080, command_handler=handle_command)
    
    # 初始状态推送
    # DashboardBridge().update_fsm_state(adjutant.fsm) # 暂只推送 Adjutant 状态

    logger.info("✓ Multi-Agent System ready.")
    logger.info("  Active Agents: " + ", ".join([a.name for a in agents]))
    logger.info("  Dashboard URL: http://localhost:8080")
    logger.info("  Press Ctrl+C to stop")

    # 5. 主循环 (Round-Robin Scheduler)
    running = True
    
    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutting down MAS...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while running:
            # 轮询每个 Agent
            for agent in agents:
                try:
                    # 执行 Agent 逻辑
                    agent.tick()
                    
                    # 临时：为了让 Dashboard 能看到状态，这里手动推送 Adjutant 的状态
                    # 未来 Dashboard 需要支持多 Agent 协议
                    if agent.name == "Adjutant" and hasattr(agent, "fsm"):
                        DashboardBridge().update_fsm_state(agent.fsm)
                        
                except Exception as e:
                    logger.error(f"Error in agent {agent.name} tick: {e}", exc_info=True)
            
            # 避免空转占用过高 CPU
            # 实际生产中可能需要根据 Agent 状态动态调整 sleep 时间
            time.sleep(0.1)

    except Exception as e:
        logger.error(f"Main loop crashed: {e}", exc_info=True)
    finally:
        logger.info("System stopped.")

if __name__ == "__main__":
    main()
