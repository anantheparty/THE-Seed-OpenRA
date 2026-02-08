"""
Commander - 简化的指挥官代理

使用新的简化架构：CodeGenNode + SimpleExecutor
"""
from __future__ import annotations

from typing import Optional, Any, Dict

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
from the_seed.config import load_config, SeedConfig
from the_seed.utils import LogManager, build_def_style_prompt

logger = LogManager.get_logger()


def build_commander(
    api: GameAPI,
    mid: RTSMiddleLayer,
    config: Optional[SeedConfig] = None
) -> SimpleExecutor:
    """
    构建简化的指挥官代理
    
    Args:
        api: GameAPI 实例
        mid: RTSMiddleLayer 中间层
        config: 可选的配置（默认从文件加载）
    
    Returns:
        SimpleExecutor: 可以直接执行命令的执行器
    """
    cfg = config or load_config()
    
    # 配置日志
    LogManager.configure(
        logfile_level=cfg.logging.logfile_level,
        console_level=cfg.logging.console_level,
        debug_mode=cfg.logging.debug_mode,
        log_dir=cfg.logging.log_dir,
    )
    logger.info(
        "配置加载完成：logfile=%s console=%s debug=%s log_dir=%s",
        cfg.logging.logfile_level,
        cfg.logging.console_level,
        cfg.logging.debug_mode,
        cfg.logging.log_dir,
    )
    
    # 创建模型
    model_config = cfg.model_templates.get(
        cfg.node_models.action,
        cfg.model_templates.get("default")
    )
    model = ModelFactory.build("codegen", model_config)
    
    # 创建代码生成节点
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
    
    # 创建执行上下文
    ctx = ExecutorContext(
        api=mid.skills,
        raw_api=api,
        api_rules=api_rules,
        runtime_globals=runtime_globals,
        observe_fn=env.observe,
    )
    
    # 创建执行器
    executor = SimpleExecutor(codegen, ctx)
    
    logger.info("Commander 构建完成")
    return executor


def build_commander_from_env(env: OpenRAEnv, config: Optional[SeedConfig] = None) -> SimpleExecutor:
    """
    从 OpenRAEnv 构建指挥官代理
    
    Args:
        env: OpenRAEnv 实例
        config: 可选的配置
    
    Returns:
        SimpleExecutor: 执行器
    """
    api = env.api
    mid = env.mid
    return build_commander(api, mid, config)
