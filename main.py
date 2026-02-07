"""
THE-Seed OpenRA - 简化版主入口

单一流程：玩家输入 → 观测 → 代码生成 → 执行
"""
from __future__ import annotations

import signal
import threading
import time

from agents.enemy_agent import EnemyAgent
from agents.nlu_gateway import Phase2NLUGateway
from adapter.openra_env import OpenRAEnv
from openra_api.game_api import GameAPI
from openra_api.jobs import JobManager, ExploreJob, AttackJob
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
from the_seed.config import load_config, ModelConfig
from the_seed.utils import LogManager, build_def_style_prompt, DashboardBridge

logger = LogManager.get_logger()

# ========== DeepSeek API 配置 ==========
DEEPSEEK_CONFIG = ModelConfig(
    request_type="openai",
    api_key="sk-d005c8cb757243949345789761078fb1",
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
    max_output_tokens=2048,
    temperature=0.7,
)

# Dashboard Bridge 端口 (与 nginx 反代配置一致)
DASHBOARD_PORT = 8090

# 玩家标识
HUMAN_PLAYER_ID = "Multi0"
ENEMY_PLAYER_ID = "Multi1"

# 敌方 AI 配置
ENEMY_TICK_INTERVAL = 45.0  # 敌方决策间隔（秒）


def setup_jobs(api: GameAPI, mid: RTSMiddleLayer) -> JobManager:
    """为 MacroActions 设置 JobManager"""
    mgr = JobManager(api=api, intel=mid.intel_service)
    mgr.add_job(ExploreJob(job_id="explore", base_radius=28))
    mgr.add_job(AttackJob(job_id="attack", step=8))
    mid.skills.jobs = mgr
    return mgr


def create_executor(api: GameAPI, mid: RTSMiddleLayer) -> SimpleExecutor:
    """创建执行器"""
    cfg = load_config()

    # 使用 DeepSeek API 配置
    model = ModelFactory.build("codegen", DEEPSEEK_CONFIG)
    logger.info(f"使用模型: {DEEPSEEK_CONFIG.model} @ {DEEPSEEK_CONFIG.base_url}")
    
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
            "query_combat_units",
            "query_actor_with_frozen",
            "unit_attribute_query",
            "query_production_queue",
            "place_building",
            "manage_production",
            "move_units",
            "attack_move",
            "attack_target",
            "stop_units",
            "repair",
            "set_rally_point",
            "player_base_info",
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


def handle_command(
    executor: SimpleExecutor,
    command: str,
    nlu_gateway: Phase2NLUGateway | None = None,
    *,
    actor: str = "human",
) -> dict:
    """处理单条命令"""
    logger.info(f"[{actor}] Processing command: {command}")

    nlu_meta = None
    if nlu_gateway is not None:
        result, nlu_meta = nlu_gateway.run(executor, command)
    else:
        result = executor.run(command)

    logger.info(
        "[%s] Command result: success=%s, message=%s%s",
        actor,
        result.success,
        result.message,
        f", source={nlu_meta.get('source')}, reason={nlu_meta.get('reason')}" if nlu_meta else "",
    )

    payload = result.to_dict()
    if nlu_meta:
        payload["nlu"] = nlu_meta
    return payload


def main() -> None:
    """主函数"""
    # ========== 人类玩家 (Multi0) ==========
    api = GameAPI(host="localhost", port=7445, language="zh", player_id=HUMAN_PLAYER_ID)
    mid = RTSMiddleLayer(api)
    human_jobs = setup_jobs(api, mid)
    executor = create_executor(api, mid)
    human_nlu_gateway = Phase2NLUGateway(name="human")
    logger.info("Human NLU status: %s", human_nlu_gateway.status())
    logger.info(f"Human player initialized: {HUMAN_PLAYER_ID}")

    # ========== 敌方 AI (Multi1) ==========
    enemy_api = GameAPI(host="localhost", port=7445, language="zh", player_id=ENEMY_PLAYER_ID)
    enemy_mid = RTSMiddleLayer(enemy_api)
    enemy_jobs = setup_jobs(enemy_api, enemy_mid)
    enemy_executor = create_executor(enemy_api, enemy_mid)
    enemy_nlu_gateway = Phase2NLUGateway(name="enemy")
    logger.info("Enemy NLU status: %s", enemy_nlu_gateway.status())

    def enemy_command_runner(command: str):
        result, _ = enemy_nlu_gateway.run(enemy_executor, command)
        return result

    dialogue_model = ModelFactory.build("enemy_dialogue", DEEPSEEK_CONFIG)
    enemy_agent = EnemyAgent(
        executor=enemy_executor,
        dialogue_model=dialogue_model,
        bridge=DashboardBridge(),
        interval=ENEMY_TICK_INTERVAL,
        command_runner=enemy_command_runner,
    )
    logger.info(f"Enemy AI initialized: {ENEMY_PLAYER_ID}, interval={ENEMY_TICK_INTERVAL}s")

    # ========== Dashboard 命令处理器 ==========
    def dashboard_handler(command: str) -> None:
        bridge = DashboardBridge()

        def status_callback(stage: str, detail: str):
            """发送阶段状态到前端"""
            bridge.broadcast("status", {
                "stage": stage,
                "detail": detail,
                "timestamp": int(time.time() * 1000)
            })

        # 动态设置状态回调
        executor.ctx.status_callback = status_callback

        # 发送接收状态
        status_callback("received", f"收到指令: {command[:50]}...")

        try:
            result = handle_command(
                executor,
                command,
                nlu_gateway=human_nlu_gateway,
                actor="human",
            )

            # 发送最终结果到 Dashboard
            bridge.broadcast("result", {
                "success": result.get("success"),
                "message": result.get("message", ""),
                "code": result.get("code", ""),
                "observations": result.get("observations", ""),
                "nlu": result.get("nlu", {}),
            })

            # 同时发送 log 保持兼容
            bridge.send_log(
                "info" if result.get("success") else "error",
                result.get("message", "")
            )
        except Exception as e:
            logger.error(f"Command failed: {e}", exc_info=True)
            bridge.broadcast("status", {"stage": "error", "detail": str(e)})
            bridge.send_log("error", f"Command failed: {str(e)}")
        finally:
            # 清除回调
            executor.ctx.status_callback = None

    # ========== 敌方控制处理器 ==========
    def enemy_control_handler(action: str, params: dict) -> None:
        if action == "start":
            enemy_agent.start()
        elif action == "stop":
            enemy_agent.stop()
        elif action == "set_interval":
            interval = params.get("interval", 45.0)
            try:
                enemy_agent.set_interval(float(interval))
            except (ValueError, TypeError):
                logger.warning(f"Invalid interval value: {interval}")
        elif action == "reset_all":
            logger.info("Reset all: clearing context and restarting enemy agent")
            # 停止敌方
            enemy_agent.stop()
            # 重置敌方上下文
            enemy_agent.reset()
            # 重置人类玩家 executor 的对话历史
            if hasattr(executor, 'codegen') and hasattr(executor.codegen, 'history'):
                executor.codegen.history.clear()
            # 重置敌方 executor 的对话历史
            if hasattr(enemy_executor, 'codegen') and hasattr(enemy_executor.codegen, 'history'):
                enemy_executor.codegen.history.clear()
            # 通知前端重置完成
            bridge = DashboardBridge()
            bridge.broadcast("reset_done", {"message": "上下文已清空"})
            # 重新启动敌方
            enemy_agent.start()
        elif action == "nlu_reload":
            human_nlu_gateway.reload()
            enemy_nlu_gateway.reload()
            bridge = DashboardBridge()
            bridge.broadcast("nlu_status", {
                "human": human_nlu_gateway.status(),
                "enemy": enemy_nlu_gateway.status(),
            })

    # ========== 启动服务 ==========
    DashboardBridge().start(
        port=DASHBOARD_PORT,
        command_handler=dashboard_handler,
        enemy_chat_handler=enemy_agent.receive_player_message,
        enemy_control_handler=enemy_control_handler,
    )
    # 敌方代理不自动启动，通过 Web 控制台手动启动

    # ========== Job tick 后台线程 ==========
    _jobs_running = True

    def _job_tick_loop():
        while _jobs_running:
            try:
                human_jobs.tick_jobs()
            except Exception:
                pass
            try:
                enemy_jobs.tick_jobs()
            except Exception:
                pass
            time.sleep(1.0)

    job_thread = threading.Thread(target=_job_tick_loop, daemon=True)
    job_thread.start()

    logger.info("=" * 50)
    logger.info("System ready")
    logger.info(f"  Dashboard WebSocket: ws://localhost:{DASHBOARD_PORT}")
    logger.info(f"  Model: {DEEPSEEK_CONFIG.model}")
    logger.info(f"  Human: {HUMAN_PLAYER_ID}")
    logger.info(f"  Enemy: {ENEMY_PLAYER_ID} (interval={ENEMY_TICK_INTERVAL}s)")
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 50)

    # 保持运行
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        enemy_agent.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        enemy_agent.stop()
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
            
            result = handle_command(
                executor,
                command,
                nlu_gateway=human_nlu_gateway,
                actor="human",
            )
            
            print(f"\n{'✓' if result.get('success') else '✗'} {result.get('message', '')}")
            
            if result.get("observations"):
                print(f"观测: {result.get('observations')}")

            nlu_meta = result.get("nlu", {})
            if nlu_meta:
                print(
                    f"NLU: {nlu_meta.get('source')} / {nlu_meta.get('reason')} "
                    f"(intent={nlu_meta.get('intent')}, conf={nlu_meta.get('confidence', 0):.3f})"
                )
            
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
