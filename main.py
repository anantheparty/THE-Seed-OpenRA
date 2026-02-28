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
import os
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

from the_seed.core import CodeGenNode, SimpleExecutor, ExecutorContext, ExecutionResult
from the_seed.model import ModelFactory
from the_seed.config import load_config
from the_seed.utils import LogManager, build_def_style_prompt, DashboardBridge

try:
    from agents.strategy.strategic_agent import StrategicAgent
except Exception:
    StrategicAgent = None

logger = LogManager.get_logger()

# Console Bridge 端口 (与 nginx 反代配置一致)
DASHBOARD_PORT = 8092

# 玩家标识
HUMAN_PLAYER_ID = "Multi0"
ENEMY_PLAYER_ID = "Multi1"

# 敌方 AI 配置
ENEMY_TICK_INTERVAL = 45.0  # 敌方决策间隔（秒）


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "y"}


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
    human_status_callback_lock = threading.Lock()
    human_status_callback_by_thread: dict[int, callable] = {}

    def human_status_dispatch(stage: str, detail: str) -> None:
        callback = None
        thread_id = threading.get_ident()
        with human_status_callback_lock:
            callback = human_status_callback_by_thread.get(thread_id)
        if callback is None:
            return
        try:
            callback(stage, detail)
        except Exception:
            pass

    executor.ctx.status_callback = human_status_dispatch

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
        if not _is_game_online():
            return ExecutionResult(
                success=False,
                message="OpenRA 未运行，Enemy指令已跳过（未调用LLM）",
                error="openra_offline",
            )
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
    strategy_map_cache: dict | None = None
    strategy_map_cache_ts = 0.0
    # AttackJob -> Strategic/Combat LLM 自动桥接（高消耗）。默认关闭，需显式开启。
    strategy_job_bridge_enabled = _env_flag("STRATEGY_AUTO_BRIDGE_ENABLED", False)
    strategy_bridge_last_sync_key = ""
    strategy_bridge_last_auto_start_ts = 0.0
    strategy_bridge_zero_attack_since_ts = 0.0
    strategy_bridge_auto_stop_grace_sec = 5.0
    game_runtime_lock = threading.RLock()
    game_runtime_online = False
    game_runtime_last_reason = "init"
    game_runtime_last_change_ms = int(time.time() * 1000)

    def _to_int_point(raw) -> dict | None:
        if not isinstance(raw, dict):
            return None
        try:
            x = int(raw.get("x"))
            y = int(raw.get("y"))
        except Exception:
            return None
        return {"x": x, "y": y}

    def _strategy_map_snapshot() -> dict:
        nonlocal strategy_map_cache, strategy_map_cache_ts
        now = time.time()
        if strategy_map_cache is not None and (now - strategy_map_cache_ts) < 0.9:
            return strategy_map_cache

        try:
            map_info = api.map_query()
            width = int(getattr(map_info, "MapWidth", 0) or 0)
            height = int(getattr(map_info, "MapHeight", 0) or 0)
            visible_grid = getattr(map_info, "IsVisible", []) or []
            explored_grid = getattr(map_info, "IsExplored", []) or []

            fog_rows: list[str] = []
            explored_count = 0
            visible_count = 0
            total_cells = max(1, width * height)

            for y in range(height):
                chars = []
                for x in range(width):
                    is_explored = False
                    is_visible = False
                    try:
                        is_explored = bool(explored_grid[x][y])
                    except Exception:
                        is_explored = False
                    try:
                        is_visible = bool(visible_grid[x][y])
                    except Exception:
                        is_visible = False

                    if is_visible:
                        chars.append("2")  # visible
                        visible_count += 1
                        explored_count += 1
                    elif is_explored:
                        chars.append("1")  # explored but fogged
                        explored_count += 1
                    else:
                        chars.append("0")  # shrouded
                fog_rows.append("".join(chars))

            resources = []
            for node in (getattr(map_info, "resourceActors", []) or [])[:512]:
                try:
                    resources.append(
                        {
                            "x": int(node.get("x")),
                            "y": int(node.get("y")),
                            "resource_type": str(node.get("resourceType", "")),
                        }
                    )
                except Exception:
                    continue

            oil_wells = []
            for well in (getattr(map_info, "oilWells", []) or [])[:128]:
                try:
                    oil_wells.append(
                        {
                            "x": int(well.get("x")),
                            "y": int(well.get("y")),
                            "owner": str(well.get("owner", "")),
                        }
                    )
                except Exception:
                    continue

            snapshot = {
                "ok": True,
                "width": width,
                "height": height,
                "fog_rows": fog_rows,
                "visible_ratio": round(visible_count / total_cells, 4),
                "explored_ratio": round(explored_count / total_cells, 4),
                "resources": resources,
                "oil_wells": oil_wells,
                "updated_ms": int(now * 1000),
            }
        except Exception as e:
            snapshot = {
                "ok": False,
                "error": str(e),
                "updated_ms": int(now * 1000),
            }

        strategy_map_cache = snapshot
        strategy_map_cache_ts = now
        return snapshot

    def _strategy_state() -> dict:
        try:
            attack_job_actor_ids = human_jobs.get_actor_ids_for_job("attack", alive_only=True)
        except Exception:
            attack_job_actor_ids = []
        with strategy_lock:
            running = bool(
                strategy_agent is not None
                and getattr(strategy_agent, "running", False)
                and strategy_thread is not None
                and strategy_thread.is_alive()
            )
            controlled_actor_ids: list[int] | None = None
            companies: list[dict] = []
            unassigned_count = 0
            player_count = 0
            company_runtime: dict[str, dict] = {}
            pending_orders: dict[str, dict] = {}

            if running and strategy_agent is not None:
                try:
                    combat_agent = getattr(strategy_agent, "combat_agent", None)
                    if combat_agent is not None:
                        getter = getattr(strategy_agent, "get_controlled_actor_ids", None)
                        if callable(getter):
                            try:
                                controlled_actor_ids = getter()
                            except Exception:
                                controlled_actor_ids = None
                        for cid, state in (getattr(combat_agent, "company_states", {}) or {}).items():
                            if not isinstance(state, dict):
                                continue
                            params = state.get("params", {}) if isinstance(state.get("params"), dict) else {}
                            target = _to_int_point(state.get("strategic_target_pos")) or _to_int_point(params.get("target_pos"))
                            company_runtime[str(cid)] = {
                                "status": str(state.get("status", "") or ""),
                                "target": target,
                            }

                        orders_lock = getattr(combat_agent, "orders_lock", None)
                        if orders_lock is not None:
                            with orders_lock:
                                pending_raw = dict(getattr(combat_agent, "pending_orders", {}) or {})
                        else:
                            pending_raw = dict(getattr(combat_agent, "pending_orders", {}) or {})

                        for cid, pending in pending_raw.items():
                            order_type = ""
                            target = None
                            if isinstance(pending, tuple) and len(pending) >= 2:
                                order_type = str(pending[0] or "")
                                params = pending[1] if isinstance(pending[1], dict) else {}
                                target = _to_int_point(params.get("target_pos"))
                            pending_orders[str(cid)] = {
                                "type": order_type,
                                "target": target,
                            }

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
                                cid_key = str(getattr(squad, "id", cid))
                                runtime = company_runtime.get(cid_key, {})
                                pending = pending_orders.get(cid_key, {})
                                target = runtime.get("target") or pending.get("target")
                                companies.append(
                                    {
                                        "id": cid_key,
                                        "name": str(getattr(squad, "name", f"Company {cid}")),
                                        "count": int(getattr(squad, "unit_count", len(members))),
                                        "power": float(round(getattr(squad, "total_score", 0.0), 2)),
                                        "weight": float(getattr(squad, "target_weight", 1.0)),
                                        "center": center,
                                        "order_status": str(runtime.get("status", "")),
                                        "target": target,
                                        "pending_order": pending if pending else None,
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

            map_snapshot = _strategy_map_snapshot()
            return {
                "available": StrategicAgent is not None,
                "game_online": _is_game_online(),
                "running": running,
                "last_error": strategy_last_error,
                "last_command": strategy_last_command,
                "companies": companies,
                "unassigned_count": unassigned_count,
                "player_count": player_count,
                "map": map_snapshot,
                "job_bridge": {
                    "enabled": bool(strategy_job_bridge_enabled),
                    "attack_job_count": len(attack_job_actor_ids),
                    "attack_job_actor_ids": attack_job_actor_ids[:256],
                    "controlled_count": None if controlled_actor_ids is None else len(controlled_actor_ids),
                    "controlled_actor_ids": None if controlled_actor_ids is None else controlled_actor_ids[:256],
                },
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

    _last_jobs_state_key = ""

    def _serialize_job_manager_state(mgr: JobManager, side: str) -> dict:
        jobs_payload: list[dict] = []
        for job in mgr.jobs:
            actor_ids = mgr.get_actor_ids_for_job(job.job_id, alive_only=True)
            jobs_payload.append(
                {
                    "job_id": str(job.job_id),
                    "name": str(getattr(job, "NAME", job.job_id)),
                    "status": str(getattr(job, "status", "")),
                    "actor_count": len(actor_ids),
                    "actor_ids": actor_ids[:256],
                    "last_summary": str(getattr(job, "last_summary", "") or ""),
                    "last_error": str(getattr(job, "last_error", "") or ""),
                }
            )
        return {
            "side": side,
            "jobs": jobs_payload,
            "actor_job": {str(k): v for k, v in sorted(mgr.actor_job.items())},
        }

    def broadcast_jobs_state(*, force: bool = False) -> None:
        nonlocal _last_jobs_state_key
        payload = {
            "timestamp": int(time.time() * 1000),
            "human": _serialize_job_manager_state(human_jobs, "human"),
            "enemy": _serialize_job_manager_state(enemy_jobs, "enemy"),
        }
        state_key = str(payload.get("human")) + "|" + str(payload.get("enemy"))
        if not force and state_key == _last_jobs_state_key:
            return
        _last_jobs_state_key = state_key
        DashboardBridge().broadcast("jobs_state", payload)

    def _strategy_set_command(command: str) -> None:
        nonlocal strategy_last_command
        command = (command or "").strip()
        if not command:
            return
        Path("user_command.txt").write_text(command, encoding="utf-8")
        strategy_last_command = command

    def _strategy_start(command: str = "") -> None:
        nonlocal strategy_agent, strategy_thread, strategy_last_error
        if not _is_game_online():
            strategy_last_error = "OpenRA 未运行，战略栈禁止启动"
            _strategy_log("warning", strategy_last_error)
            _broadcast_strategy_state()
            return
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
                try:
                    strategy_agent.set_controlled_actor_ids(human_jobs.get_actor_ids_for_job("attack", alive_only=True))
                except Exception:
                    pass
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

    def _set_attack_job_external_control(enabled: bool) -> None:
        try:
            job = human_jobs.get_job("attack")
            if isinstance(job, AttackJob):
                job.set_externally_controlled(enabled)
        except Exception:
            pass

    def _probe_game_online() -> bool:
        try:
            return bool(GameAPI.is_server_running(host="localhost", port=7445, timeout=0.6))
        except Exception:
            return False

    def _is_game_online() -> bool:
        with game_runtime_lock:
            return bool(game_runtime_online)

    def _broadcast_game_runtime_state(reason: str = "") -> None:
        with game_runtime_lock:
            payload = {
                "online": bool(game_runtime_online),
                "reason": str(reason or game_runtime_last_reason),
                "changed_at": int(game_runtime_last_change_ms),
                "timestamp": int(time.time() * 1000),
            }
        DashboardBridge().broadcast("game_runtime_state", payload)

    def _refresh_game_runtime_state(*, reason: str = "periodic_probe", force_broadcast: bool = False) -> bool:
        nonlocal game_runtime_online, game_runtime_last_reason, game_runtime_last_change_ms

        online = _probe_game_online()
        changed = False
        with game_runtime_lock:
            prev = bool(game_runtime_online)
            changed = online != prev
            if changed:
                game_runtime_online = online
                game_runtime_last_change_ms = int(time.time() * 1000)
            game_runtime_last_reason = str(reason or game_runtime_last_reason)

        if changed:
            if online:
                logger.info("OpenRA 连接已恢复，系统解除离线闸门")
            else:
                logger.warning("OpenRA 未运行/不可达，系统进入离线闸门（停止后台代理与任务）")
                _set_attack_job_external_control(False)
                with strategy_lock:
                    strategy_running = bool(
                        strategy_agent is not None
                        and getattr(strategy_agent, "running", False)
                        and strategy_thread is not None
                        and strategy_thread.is_alive()
                    )
                if strategy_running:
                    _strategy_stop()
                if bool(getattr(enemy_agent, "running", False)):
                    enemy_agent.stop()

        if changed or force_broadcast:
            _broadcast_game_runtime_state(reason=reason)

        return online

    def _sync_attack_job_to_strategy() -> None:
        nonlocal strategy_bridge_last_sync_key
        nonlocal strategy_bridge_last_auto_start_ts
        nonlocal strategy_bridge_zero_attack_since_ts
        nonlocal strategy_last_error
        if not _is_game_online():
            with strategy_lock:
                running = bool(
                    strategy_agent is not None
                    and getattr(strategy_agent, "running", False)
                    and strategy_thread is not None
                    and strategy_thread.is_alive()
                )
            _set_attack_job_external_control(False)
            strategy_bridge_zero_attack_since_ts = 0.0
            if running:
                _strategy_log("info", "OpenRA 离线，停止战略栈")
                _strategy_stop()
            return

        if not strategy_job_bridge_enabled:
            with strategy_lock:
                running = bool(
                    strategy_agent is not None
                    and getattr(strategy_agent, "running", False)
                    and strategy_thread is not None
                    and strategy_thread.is_alive()
                )
            _set_attack_job_external_control(False)
            strategy_bridge_zero_attack_since_ts = 0.0
            if running:
                _strategy_log("info", "战略自动桥接已关闭，停止战略栈")
                _strategy_stop()
            return

        try:
            attack_job_actor_ids = human_jobs.get_actor_ids_for_job("attack", alive_only=True)
        except Exception:
            attack_job_actor_ids = []
        attack_count = len(attack_job_actor_ids)

        with strategy_lock:
            running = bool(
                strategy_agent is not None
                and getattr(strategy_agent, "running", False)
                and strategy_thread is not None
                and strategy_thread.is_alive()
            )
            local_agent = strategy_agent if running else None

        controlled_count = 0
        if local_agent is not None:
            try:
                local_agent.set_controlled_actor_ids(attack_job_actor_ids)
                controlled = local_agent.get_controlled_actor_ids()
                controlled_count = len(controlled or [])
                if attack_count > 0:
                    strategy_bridge_zero_attack_since_ts = 0.0
                    _set_attack_job_external_control(True)
                else:
                    _set_attack_job_external_control(False)
                    now = time.time()
                    if strategy_bridge_zero_attack_since_ts <= 0:
                        strategy_bridge_zero_attack_since_ts = now
                    elif (now - strategy_bridge_zero_attack_since_ts) >= strategy_bridge_auto_stop_grace_sec:
                        _strategy_log("info", "AttackJob 已清空，战略栈自动停止")
                        _strategy_stop()
                        strategy_bridge_zero_attack_since_ts = 0.0
                        return
            except Exception as e:
                strategy_last_error = str(e)
                _strategy_log("error", f"AttackJob->Strategy 同步失败: {e}")
        else:
            _set_attack_job_external_control(False)
            strategy_bridge_zero_attack_since_ts = 0.0
            now = time.time()
            if (
                attack_count > 0
                and StrategicAgent is not None
                and (now - strategy_bridge_last_auto_start_ts) >= 3.0
            ):
                strategy_bridge_last_auto_start_ts = now
                _strategy_log("info", f"检测到 AttackJob 单位({attack_count})，自动启动战略栈并接管")
                _strategy_start("执行 AttackJob：集中兵力清剿敌军并摧毁建筑")

        sync_key = f"run={int(running)}:attack={attack_count}:controlled={controlled_count}"
        if sync_key != strategy_bridge_last_sync_key:
            strategy_bridge_last_sync_key = sync_key
            _broadcast_strategy_state()

    # ========== Console 命令处理器 ==========
    def copilot_command_handler(command: str, meta: dict | None = None) -> None:
        bridge = DashboardBridge()
        start_ts = int(time.time() * 1000)
        thread_id = threading.get_ident()
        payload_meta = meta or {}
        command_id = str(payload_meta.get("command_id") or f"cmd_{start_ts}_{thread_id}")

        def status_callback(stage: str, detail: str):
            """发送阶段状态到前端"""
            bridge.broadcast("status", {
                "stage": stage,
                "detail": detail,
                "command_id": command_id,
                "timestamp": int(time.time() * 1000)
            })

        with human_status_callback_lock:
            human_status_callback_by_thread[thread_id] = status_callback

        # 发送接收状态
        status_callback("received", f"收到指令: {command[:50]}...")

        try:
            if not _refresh_game_runtime_state(reason="human_command_precheck"):
                blocked_msg = "OpenRA 未运行，指令已拦截（未执行、未调用LLM）"
                status_callback("error", blocked_msg)
                result = {
                    "success": False,
                    "message": blocked_msg,
                    "code": "",
                    "observations": "",
                    "nlu": {
                        "source": "game_gate",
                        "reason": "openra_offline",
                    },
                }
                append_interaction_event(
                    "dashboard_command_blocked",
                    {
                        "actor": "human",
                        "channel": "dashboard_command",
                        "command_id": command_id,
                        "utterance": command,
                        "response_message": blocked_msg,
                        "success": False,
                        "nlu": result["nlu"],
                    },
                    timestamp_ms=start_ts,
                )
                bridge.broadcast(
                    "result",
                    {
                        "success": False,
                        "message": blocked_msg,
                        "code": "",
                        "observations": "",
                        "nlu": result["nlu"],
                        "command_id": command_id,
                    },
                )
                bridge.send_log("warning", blocked_msg)
                return

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
                    "command_id": command_id,
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
                "command_id": command_id,
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
                    "command_id": command_id,
                    "utterance": command,
                    "error": str(e),
                },
                timestamp_ms=start_ts,
            )
            bridge.broadcast("status", {"stage": "error", "detail": str(e), "command_id": command_id})
            bridge.broadcast(
                "result",
                {
                    "success": False,
                    "message": f"执行失败: {e}",
                    "code": "",
                    "observations": "",
                    "nlu": {},
                    "command_id": command_id,
                },
            )
            bridge.send_log("error", f"Command failed: {str(e)}")
        finally:
            with human_status_callback_lock:
                human_status_callback_by_thread.pop(thread_id, None)

    # ========== 敌方控制处理器 ==========
    def enemy_control_handler(action: str, params: dict) -> None:
        nonlocal strategy_job_bridge_enabled
        if action == "start":
            if not _refresh_game_runtime_state(reason="enemy_start_precheck"):
                msg = "OpenRA 未运行，已阻止启动敌方AI"
                DashboardBridge().broadcast(
                    "enemy_status",
                    {"stage": "offline", "detail": msg, "timestamp": int(time.time() * 1000)},
                )
                DashboardBridge().broadcast("enemy_agent_state", enemy_agent.get_state())
                logger.warning(msg)
                return
            enemy_agent.start()
        elif action == "stop":
            enemy_agent.stop()
        elif action == "status":
            DashboardBridge().broadcast("enemy_agent_state", enemy_agent.get_state())
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
            executor.ctx.history.clear()
            # 重置敌方 executor 的对话历史
            enemy_executor.ctx.history.clear()
            # 清空共享聊天历史
            bridge = DashboardBridge()
            bridge.clear_chat_history()
            # 通知前端重置完成
            bridge.broadcast("reset_done", {"message": "上下文已清空"})
            # 仅在游戏在线时重新启动敌方
            if _refresh_game_runtime_state(reason="reset_all_postcheck"):
                enemy_agent.start()
            else:
                DashboardBridge().broadcast(
                    "enemy_status",
                    {"stage": "offline", "detail": "OpenRA 未运行，已跳过敌方重启", "timestamp": int(time.time() * 1000)},
                )
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
            if not _refresh_game_runtime_state(reason="strategy_start_precheck"):
                _strategy_log("warning", "OpenRA 未运行，无法启用战略自动桥接")
                _broadcast_strategy_state()
                return
            strategy_job_bridge_enabled = True
            _strategy_log("info", "战略自动桥接已启用（AttackJob 可触发战略/战斗 LLM）")
            _sync_attack_job_to_strategy()
            _broadcast_strategy_state()
        elif action == "strategy_stop":
            strategy_job_bridge_enabled = False
            _set_attack_job_external_control(False)
            _strategy_stop()
            _strategy_log("info", "战略自动桥接已停用（后台战略/战斗 LLM 已停止）")
            _broadcast_strategy_state()
        elif action == "strategy_cmd":
            cmd = str(params.get("command", "") or "").strip()
            if not cmd:
                _strategy_log("warning", "空战略指令，已忽略")
                _broadcast_strategy_state()
                return
            _strategy_set_command(cmd)
            with strategy_lock:
                running = bool(
                    strategy_agent is not None
                    and getattr(strategy_agent, "running", False)
                    and strategy_thread is not None
                    and strategy_thread.is_alive()
                )
            if running:
                _strategy_log("info", f"战略指令已更新: {cmd}")
            else:
                _strategy_log("info", f"战略指令已保存，等待 AttackJob 激活后自动生效: {cmd}")
            _broadcast_strategy_state()
        elif action == "strategy_status":
            _broadcast_strategy_state()
        elif action == "jobs_status":
            broadcast_jobs_state(force=True)

    # ========== 启动服务 ==========
    def enemy_chat_handler(message: str) -> None:
        if not _refresh_game_runtime_state(reason="enemy_chat_precheck"):
            DashboardBridge().broadcast(
                "enemy_chat",
                {"message": "OpenRA 未运行，敌方聊天已禁用（未调用LLM）", "type": "system"},
            )
            return
        enemy_agent.receive_player_message(message)

    DashboardBridge().start(
        port=DASHBOARD_PORT,
        command_handler=copilot_command_handler,
        enemy_chat_handler=enemy_chat_handler,
        enemy_control_handler=enemy_control_handler,
    )
    # 敌方代理不自动启动，通过 Web 控制台手动启动
    _refresh_game_runtime_state(reason="startup_probe", force_broadcast=True)
    _broadcast_strategy_state()
    _sync_attack_job_to_strategy()
    broadcast_jobs_state(force=True)

    # ========== Job tick 后台线程 ==========
    _jobs_running = True
    _last_human_explore_summary = ""

    def _job_tick_loop():
        nonlocal _last_human_explore_summary
        while _jobs_running:
            if not _refresh_game_runtime_state(reason="job_tick_probe"):
                try:
                    _broadcast_strategy_state()
                    broadcast_jobs_state(force=True)
                except Exception:
                    pass
                time.sleep(1.0)
                continue
            try:
                human_jobs.tick_jobs()
                explore_job = human_jobs.get_job("explore")
                if explore_job is not None:
                    summary = str(getattr(explore_job, "last_summary", "") or "")
                    if summary and summary != _last_human_explore_summary:
                        logger.info("ExploreJob[h] %s", summary)
                        _last_human_explore_summary = summary
            except Exception as e:
                logger.warning("human_jobs.tick_jobs failed: %s", e, exc_info=True)
            try:
                _sync_attack_job_to_strategy()
            except Exception as e:
                logger.warning("_sync_attack_job_to_strategy failed: %s", e, exc_info=True)
            try:
                enemy_jobs.tick_jobs()
            except Exception as e:
                logger.warning("enemy_jobs.tick_jobs failed: %s", e, exc_info=True)
            try:
                broadcast_jobs_state()
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
    logger.info("  Strategy Auto Bridge: %s", "ON" if strategy_job_bridge_enabled else "OFF")
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

            if not GameAPI.is_server_running(host="localhost", port=7445, timeout=0.6):
                print("\n✗ OpenRA 未运行，指令已拦截（未执行、未调用LLM）")
                continue
            
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
