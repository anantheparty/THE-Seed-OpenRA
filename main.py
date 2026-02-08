"""
THE-Seed OpenRA - 简化版主入口

单一流程：玩家输入 → 观测 → 代码生成 → 执行
"""
from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path

import yaml

from agents.enemy_agent import EnemyAgent
from agents.nlu_gateway import Phase2NLUGateway
from nlu_pipeline.interaction_logger import append_interaction_event
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
from the_seed.config import load_config
from the_seed.utils import LogManager, build_def_style_prompt, DashboardBridge

try:
    from agents.strategy.strategic_agent import StrategicAgent
except Exception:
    StrategicAgent = None

logger = LogManager.get_logger()

# Console Bridge 端口 (与 nginx 反代配置一致)
DASHBOARD_PORT = 8090

# 玩家标识
HUMAN_PLAYER_ID = "Multi0"
ENEMY_PLAYER_ID = "Multi1"

# 敌方 AI 配置
ENEMY_TICK_INTERVAL = 45.0  # 敌方决策间隔（秒）


def resolve_model_config():
    cfg = load_config()
    template_name = getattr(cfg.node_models, "action", "default")
    model_cfg = cfg.model_templates.get(template_name) or cfg.model_templates.get("default")
    if model_cfg is None:
        raise RuntimeError("model_templates is empty in the-seed config")
    return model_cfg


def setup_jobs(api: GameAPI, mid: RTSMiddleLayer) -> JobManager:
    """为 MacroActions 设置 JobManager"""
    mgr = JobManager(api=api, intel=mid.intel_service)
    mgr.add_job(ExploreJob(job_id="explore", base_radius=28))
    mgr.add_job(AttackJob(job_id="attack", step=8))
    mid.skills.jobs = mgr
    return mgr


def create_executor(api: GameAPI, mid: RTSMiddleLayer) -> SimpleExecutor:
    """创建执行器"""
    model_cfg = resolve_model_config()
    model = ModelFactory.build("codegen", model_cfg)
    logger.info("使用模型: %s @ %s", model_cfg.model, model_cfg.base_url)
    
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
        result, nlu_meta = nlu_gateway.run(executor, command, rollout_key=actor)
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
    runtime_gateway_cfg_path = Path("nlu_pipeline/configs/runtime_gateway.yaml")
    project_root = Path(__file__).resolve().parent

    def enemy_command_runner(command: str):
        result, _ = enemy_nlu_gateway.run(enemy_executor, command, rollout_key="enemy_agent")
        return result

    def mutate_runtime_gateway_config(mutator) -> dict:
        cfg = {}
        if runtime_gateway_cfg_path.exists():
            with runtime_gateway_cfg_path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        mutator(cfg)
        runtime_gateway_cfg_path.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return cfg

    def broadcast_nlu_status() -> None:
        bridge = DashboardBridge()
        bridge.broadcast(
            "nlu_status",
            {
                "human": human_nlu_gateway.status(),
                "enemy": enemy_nlu_gateway.status(),
            },
        )

    def run_nlu_job(
        *,
        action: str,
        script: str,
        report_path: str,
        extra_args: list[str] | None = None,
    ) -> None:
        bridge = DashboardBridge()
        extra_args = extra_args or []
        cmd = [sys.executable, script, *extra_args]
        bridge.broadcast(
            "nlu_job_status",
            {
                "action": action,
                "stage": "start",
                "cmd": cmd,
                "timestamp": int(time.time() * 1000),
            },
        )
        try:
            proc = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = {
                "action": action,
                "stage": "done",
                "returncode": int(proc.returncode),
                "stdout_tail": (proc.stdout or "")[-4000:],
                "stderr_tail": (proc.stderr or "")[-4000:],
                "report_path": report_path,
                "timestamp": int(time.time() * 1000),
            }
            report_file = project_root / report_path
            if report_file.exists():
                try:
                    payload["report"] = yaml.safe_load(report_file.read_text(encoding="utf-8"))
                except Exception:
                    payload["report_raw"] = report_file.read_text(encoding="utf-8", errors="ignore")[-4000:]
            bridge.broadcast("nlu_job_status", payload)
        except Exception as e:
            bridge.broadcast(
                "nlu_job_status",
                {
                    "action": action,
                    "stage": "error",
                    "error": str(e),
                    "timestamp": int(time.time() * 1000),
                },
            )

    runtime_model_cfg = resolve_model_config()
    dialogue_model = ModelFactory.build("enemy_dialogue", runtime_model_cfg)
    enemy_agent = EnemyAgent(
        executor=enemy_executor,
        dialogue_model=dialogue_model,
        bridge=DashboardBridge(),
        interval=ENEMY_TICK_INTERVAL,
        command_runner=enemy_command_runner,
    )
    logger.info(f"Enemy AI initialized: {ENEMY_PLAYER_ID}, interval={ENEMY_TICK_INTERVAL}s")

    # ========== Strategy Stack (Debug Preview) ==========
    strategy_agent = None
    strategy_thread = None
    strategy_last_error = ""
    strategy_last_command = ""
    strategy_lock = threading.RLock()

    def _strategy_state() -> dict:
        with strategy_lock:
            running = bool(
                strategy_agent is not None
                and getattr(strategy_agent, "running", False)
                and strategy_thread is not None
                and strategy_thread.is_alive()
            )
            companies: list[dict] = []
            unassigned_count = 0
            player_count = 0

            if running and strategy_agent is not None:
                try:
                    squad_manager = getattr(strategy_agent, "squad_manager", None)
                    if squad_manager is not None:
                        squad_lock = getattr(squad_manager, "lock", None)
                        lock_ctx = squad_lock if squad_lock is not None else nullcontext()
                        with lock_ctx:
                            company_items = sorted(
                                getattr(squad_manager, "companies", {}).items(),
                                key=lambda item: str(item[0]),
                            )
                            for cid, squad in company_items:
                                center = None
                                try:
                                    center = squad.get_center_coordinates()
                                except Exception:
                                    center = None
                                members = []
                                unit_items = sorted(getattr(squad, "units", {}).items(), key=lambda item: int(item[0]))
                                for _, unit in unit_items:
                                    pos = getattr(unit, "position", {}) or {}
                                    hp_ratio_raw = getattr(unit, "hp_ratio", 0.0)
                                    try:
                                        hp_ratio = float(hp_ratio_raw)
                                    except Exception:
                                        hp_ratio = 0.0
                                    hp_percent = max(0, min(100, int(round(hp_ratio * 100))))
                                    members.append(
                                        {
                                            "id": int(getattr(unit, "id", -1)),
                                            "type": str(getattr(unit, "type", "")),
                                            "category": str(getattr(unit, "category", "")),
                                            "hp_percent": hp_percent,
                                            "score": float(getattr(unit, "score", 0.0)),
                                            "position": {
                                                "x": pos.get("x"),
                                                "y": pos.get("y"),
                                            },
                                        }
                                    )
                                companies.append(
                                    {
                                        "id": str(getattr(squad, "id", cid)),
                                        "name": str(getattr(squad, "name", f"Company {cid}")),
                                        "count": int(getattr(squad, "unit_count", len(members))),
                                        "power": float(round(getattr(squad, "total_score", 0.0), 2)),
                                        "weight": float(getattr(squad, "target_weight", 1.0)),
                                        "center": center,
                                        "members": members,
                                    }
                                )
                            unassigned = getattr(squad_manager, "unassigned", None)
                            if unassigned is not None:
                                unassigned_count = int(getattr(unassigned, "unit_count", 0))
                            player_squad = getattr(squad_manager, "player_squad", None)
                            if player_squad is not None:
                                player_count = int(getattr(player_squad, "unit_count", 0))
                except Exception as e:
                    logger.warning("Build strategy roster state failed: %s", e)

            return {
                "available": StrategicAgent is not None,
                "running": running,
                "last_error": strategy_last_error,
                "last_command": strategy_last_command,
                "companies": companies,
                "unassigned_count": unassigned_count,
                "player_count": player_count,
            }

    def _broadcast_strategy_state() -> None:
        DashboardBridge().broadcast("strategy_state", _strategy_state())

    def _strategy_log(level: str, message: str) -> None:
        DashboardBridge().broadcast(
            "strategy_log",
            {
                "level": level,
                "message": message,
                "timestamp": int(time.time() * 1000),
            },
        )

    def _strategy_trace(event: str, payload: dict | None = None) -> None:
        DashboardBridge().broadcast(
            "strategy_trace",
            {
                "event": str(event or ""),
                "payload": payload or {},
                "timestamp": int(time.time() * 1000),
            },
        )

    def _strategy_set_command(command: str) -> None:
        nonlocal strategy_last_command
        command = (command or "").strip()
        if not command:
            return
        Path("user_command.txt").write_text(command, encoding="utf-8")
        strategy_last_command = command

    def _strategy_start(command: str = "") -> None:
        nonlocal strategy_agent, strategy_thread, strategy_last_error
        if StrategicAgent is None:
            strategy_last_error = "StrategicAgent import failed"
            _strategy_log("error", strategy_last_error)
            _broadcast_strategy_state()
            return
        with strategy_lock:
            if strategy_thread is not None and strategy_thread.is_alive():
                _strategy_log("info", "战略栈已在运行")
                _broadcast_strategy_state()
                return
            try:
                if command:
                    _strategy_set_command(command)
                strategy_agent = StrategicAgent(trace_callback=_strategy_trace)
                strategy_thread = threading.Thread(target=strategy_agent.start, daemon=True, name="StrategyAgent")
                strategy_thread.start()
                strategy_last_error = ""
                _strategy_log("info", "战略栈已启动（实验模式）")
            except Exception as e:
                strategy_last_error = str(e)
                _strategy_log("error", f"战略栈启动失败: {e}")
            finally:
                _broadcast_strategy_state()

    def _strategy_stop() -> None:
        nonlocal strategy_agent, strategy_thread
        with strategy_lock:
            if strategy_agent is not None:
                try:
                    strategy_agent.stop()
                except Exception:
                    pass
            if strategy_thread is not None and strategy_thread.is_alive():
                strategy_thread.join(timeout=2.0)
            strategy_agent = None
            strategy_thread = None
            _strategy_log("info", "战略栈已停止")
            _broadcast_strategy_state()

    # ========== Console 命令处理器 ==========
    def copilot_command_handler(command: str) -> None:
        bridge = DashboardBridge()
        start_ts = int(time.time() * 1000)

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
            append_interaction_event(
                "dashboard_command",
                {
                    "actor": "human",
                    "channel": "dashboard_command",
                    "utterance": command,
                    "response_message": result.get("message", ""),
                    "success": bool(result.get("success", False)),
                    "observations": result.get("observations", ""),
                    "nlu": result.get("nlu", {}) or {},
                },
                timestamp_ms=start_ts,
            )

            # 发送最终结果到 Console
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
            append_interaction_event(
                "dashboard_command_error",
                {
                    "actor": "human",
                    "channel": "dashboard_command",
                    "utterance": command,
                    "error": str(e),
                },
                timestamp_ms=start_ts,
            )
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
            _strategy_stop()
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
            broadcast_nlu_status()
        elif action == "nlu_set_rollout":
            try:
                target_agent = str(params.get("agent", "")).strip()
                percentage_raw = params.get("percentage")
                enabled_raw = params.get("enabled")
                bucket_key = params.get("bucket_key")

                def _mutator(cfg: dict) -> None:
                    rollout = cfg.setdefault("rollout", {})
                    if enabled_raw is not None:
                        rollout["enabled"] = bool(enabled_raw)
                    if percentage_raw is not None:
                        pct = max(0.0, min(100.0, float(percentage_raw)))
                        if target_agent:
                            by_agent = rollout.setdefault("percentages_by_agent", {})
                            if isinstance(by_agent, dict):
                                by_agent[target_agent] = pct
                        else:
                            rollout["default_percentage"] = pct
                    if bucket_key is not None:
                        rollout["bucket_key"] = str(bucket_key)

                cfg = mutate_runtime_gateway_config(_mutator)
                human_nlu_gateway.reload()
                enemy_nlu_gateway.reload()
                DashboardBridge().broadcast(
                    "nlu_rollout_updated",
                    {
                        "agent": target_agent,
                        "runtime_config_path": str(runtime_gateway_cfg_path),
                        "rollout": cfg.get("rollout", {}),
                    },
                )
                broadcast_nlu_status()
            except Exception as e:
                logger.error("nlu_set_rollout failed: %s", e, exc_info=True)
                DashboardBridge().broadcast("nlu_rollout_updated", {"error": str(e)})
        elif action == "nlu_set_shadow":
            try:
                shadow_mode = bool(params.get("shadow_mode", True))
                enabled_raw = params.get("enabled")

                def _mutator(cfg: dict) -> None:
                    cfg["shadow_mode"] = shadow_mode
                    if enabled_raw is not None:
                        cfg["enabled"] = bool(enabled_raw)

                mutate_runtime_gateway_config(_mutator)
                human_nlu_gateway.reload()
                enemy_nlu_gateway.reload()
                broadcast_nlu_status()
            except Exception as e:
                logger.error("nlu_set_shadow failed: %s", e, exc_info=True)
                DashboardBridge().broadcast("nlu_status", {"error": str(e)})
        elif action == "nlu_emergency_rollback":
            try:
                def _mutator(cfg: dict) -> None:
                    cfg["enabled"] = False
                    cfg["shadow_mode"] = False
                    cfg["phase"] = "phase4_manual_rollback"
                    rollout = cfg.setdefault("rollout", {})
                    rollout["enabled"] = True
                    rollout["default_percentage"] = 0
                    by_agent = rollout.get("percentages_by_agent", {})
                    if isinstance(by_agent, dict):
                        for k in list(by_agent.keys()):
                            by_agent[k] = 0
                        rollout["percentages_by_agent"] = by_agent

                mutate_runtime_gateway_config(_mutator)
                human_nlu_gateway.reload()
                enemy_nlu_gateway.reload()
                DashboardBridge().broadcast(
                    "nlu_rollback_done",
                    {
                        "phase": "phase4_manual_rollback",
                        "runtime_config_path": str(runtime_gateway_cfg_path),
                    },
                )
                broadcast_nlu_status()
            except Exception as e:
                logger.error("nlu_emergency_rollback failed: %s", e, exc_info=True)
                DashboardBridge().broadcast("nlu_rollback_done", {"error": str(e)})
        elif action == "nlu_status":
            broadcast_nlu_status()
        elif action == "nlu_phase6_runtest":
            run_nlu_job(
                action=action,
                script="nlu_pipeline/scripts/runtime_runtest.py",
                report_path="nlu_pipeline/reports/phase6_runtest_report.json",
            )
        elif action == "nlu_release_bundle":
            run_nlu_job(
                action=action,
                script="nlu_pipeline/scripts/release_bundle.py",
                report_path="nlu_pipeline/reports/phase5_release_report.json",
            )
        elif action == "nlu_smoke":
            run_nlu_job(
                action=action,
                script="nlu_pipeline/scripts/run_smoke.py",
                report_path="nlu_pipeline/reports/smoke_report.json",
            )
        elif action == "strategy_start":
            _strategy_start(str(params.get("command", "") or ""))
        elif action == "strategy_stop":
            _strategy_stop()
        elif action == "strategy_cmd":
            cmd = str(params.get("command", "") or "").strip()
            if not cmd:
                _strategy_log("warning", "空战略指令，已忽略")
                _broadcast_strategy_state()
                return
            with strategy_lock:
                running = bool(
                    strategy_agent is not None
                    and getattr(strategy_agent, "running", False)
                    and strategy_thread is not None
                    and strategy_thread.is_alive()
                )
            if not running:
                _strategy_log("info", "战略栈未运行，已按指令自动启动")
                _strategy_start(cmd)
            else:
                _strategy_set_command(cmd)
                _strategy_log("info", f"战略指令已更新: {cmd}")
                _broadcast_strategy_state()
        elif action == "strategy_status":
            _broadcast_strategy_state()

    # ========== 启动服务 ==========
    DashboardBridge().start(
        port=DASHBOARD_PORT,
        command_handler=copilot_command_handler,
        enemy_chat_handler=enemy_agent.receive_player_message,
        enemy_control_handler=enemy_control_handler,
    )
    # 敌方代理不自动启动，通过 Web 控制台手动启动
    _broadcast_strategy_state()

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
    logger.info(f"  Console WebSocket: ws://localhost:{DASHBOARD_PORT}")
    logger.info("  Model: %s", runtime_model_cfg.model)
    logger.info(f"  Human: {HUMAN_PLAYER_ID}")
    logger.info(f"  Enemy: {ENEMY_PLAYER_ID} (interval={ENEMY_TICK_INTERVAL}s)")
    logger.info("  Press Ctrl+C to stop")
    logger.info("=" * 50)

    # 保持运行
    def signal_handler(sig, frame):
        logger.info("Shutting down...")
        _strategy_stop()
        enemy_agent.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        _strategy_stop()
        enemy_agent.stop()
        logger.info("Backend stopped.")


def main_cli() -> None:
    """CLI 模式 - 用于测试"""
    api = GameAPI(host="localhost", port=7445, language="zh")
    mid = RTSMiddleLayer(api)
    executor = create_executor(api, mid)
    human_nlu_gateway = Phase2NLUGateway(name="human_cli")
    
    logger.info("✓ CLI mode ready. Type commands or 'quit' to exit.")
    
    while True:
        try:
            command = input("\n> ").strip()
            
            if not command:
                continue
            
            if command.lower() in ("quit", "exit", "q"):
                break
            
            result = handle_command(executor, command, nlu_gateway=human_nlu_gateway, actor="human")
            
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
