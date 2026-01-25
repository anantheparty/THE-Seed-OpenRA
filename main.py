"""
THE-Seed OpenRA - 简化版主入口

单一流程：玩家输入 → 观测 → 代码生成 → 执行
"""
from __future__ import annotations

import signal
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
    """创建执行器"""
    cfg = load_config()
    
    # 使用 action 模型配置
    model_config = cfg.model_templates.get(cfg.node_models.action, cfg.model_templates.get("default"))
    model = ModelFactory.build("codegen", model_config)
    
    codegen = CodeGenNode(model)
    
    # 创建环境
    env = OpenRAEnv(api)
    
    # 构建 API 文档
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
    
    # 运行时全局变量
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
    
    ctx = ExecutorContext(
        api=mid.skills,
        raw_api=api,
        api_rules=api_rules,
        runtime_globals=runtime_globals,
        observe_fn=env.observe,
    )
    
    return SimpleExecutor(codegen, ctx)


def handle_command(executor: SimpleExecutor, command: str) -> dict:
    """处理单条命令"""
    logger.info(f"Processing command: {command}")
    
    result = executor.run(command)
    
    logger.info(f"Command result: success={result.success}, message={result.message}")
    
    return result.to_dict()


def main() -> None:
    """主函数"""
    # 初始化 API
    api = GameAPI(host="localhost", port=7445, language="zh")
    mid = RTSMiddleLayer(api)
    
    # 创建执行器
    executor = create_executor(api, mid)
    
    # Dashboard 命令处理器
    def dashboard_handler(command: str) -> None:
        try:
            result = handle_command(executor, command)
            
            # 发送结果到 Dashboard
            DashboardBridge().send_log(
                "info" if result.get("success") else "error",
                result.get("message", "")
            )
        except Exception as e:
            logger.error(f"Command failed: {e}", exc_info=True)
            DashboardBridge().send_log("error", f"Command failed: {str(e)}")
    
    # 启动 Dashboard Bridge
    DashboardBridge().start(port=8080, command_handler=dashboard_handler)
    
    logger.info("✓ System ready (simplified mode)")
    logger.info("  Dashboard URL: http://localhost:8080")
    logger.info("  Press Ctrl+C to stop")
    
    # 保持运行
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        raise SystemExit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Backend stopped.")


def main_cli() -> None:
    """CLI 模式 - 用于测试"""
    api = GameAPI(host="localhost", port=7445, language="zh")
    mid = RTSMiddleLayer(api)
    
    executor = create_executor(api, mid)
    
    logger.info("✓ CLI mode ready. Type commands or 'quit' to exit.")
    
    while True:
        try:
            command = input("\n> ").strip()
            
            if not command:
                continue
            
            if command.lower() in ("quit", "exit", "q"):
                break
            
            result = handle_command(executor, command)
            
            print(f"\n{'✓' if result.get('success') else '✗'} {result.get('message', '')}")
            
            if result.get("observations"):
                print(f"观测: {result.get('observations')}")
            
            if not result.get("success") and result.get("error"):
                print(f"错误: {result.get('error')}")
        
        except EOFError:
            break
        except KeyboardInterrupt:
            print("\n")
            break
    
    logger.info("CLI stopped.")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        main_cli()
    else:
        main()
