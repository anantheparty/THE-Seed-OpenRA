from __future__ import annotations
from typing import Optional, Any, Dict
from dataclasses import is_dataclass, fields

# —— the-seed 组件 ——
from the_seed.core.runtime import SeedRuntime
from the_seed.core.registry import ActionRegistry
from the_seed.core.factory import NodeFactory
from the_seed.utils.log_manager import LogManager
from the_seed.config import load_config, SeedConfig
from the_seed.config.schema import AgentTemplateSection

# —— OpenRA 环境适配 ——
from adapter.openra_env import OpenRAEnv

logger = LogManager.get_logger()

def build_commander(env: OpenRAEnv, config: Optional[SeedConfig] = None) -> SeedRuntime:
    cfg = config or load_config()
    LogManager.configure(
        logfile_level=cfg.logging.logfile_level,
        console_level=cfg.logging.console_level,
        debug_mode=cfg.logging.debug_mode,
        log_dir=cfg.logging.log_dir,
    )
    logger.info(
        "加载配置完成：logfile=%s console=%s debug=%s log_dir=%s",
        cfg.logging.logfile_level,
        cfg.logging.console_level,
        cfg.logging.debug_mode,
        cfg.logging.log_dir,
    )
    template = _resolve_template(cfg)

    # 注册动作
    reg = ActionRegistry()
    env.register_actions(reg)
    logger.info("Commander 注册完所有动作")

    factory = NodeFactory()
    agent = factory.build()

    runtime = SeedRuntime(
        env_observe=env.observe,
        agent=agent,
        cfg=cfg.runtime,
        context_enricher=_make_context_enricher(env),
    )
    return runtime


def _resolve_template(cfg: SeedConfig) -> AgentTemplateSection:
    templates = cfg.agent_templates or {}
    template = templates.get(cfg.active_agent_template)
    if template:
        return _coerce_template(template)
    if templates:
        logger.warning("未找到模板 %s，回退到第一个", cfg.active_agent_template)
        return _coerce_template(next(iter(templates.values())))
    logger.warning("配置中未定义 agent 模板，使用默认模板")
    return AgentTemplateSection()


def _make_context_enricher(env: OpenRAEnv):
    def enrich(ctx):
        ctx.api = getattr(env, "api", None)
        return ctx

    return enrich


def _coerce_template(raw: Any) -> AgentTemplateSection:
    if isinstance(raw, AgentTemplateSection):
        return raw
    template = AgentTemplateSection()
    if isinstance(raw, dict):
        _apply_dict_to_dataclass(template, raw)
    return template


def _apply_dict_to_dataclass(instance: Any, data: Dict[str, Any]) -> None:
    for f in fields(instance):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(instance, f.name)
        if is_dataclass(current):
            if isinstance(value, dict):
                _apply_dict_to_dataclass(current, value)
        else:
            setattr(instance, f.name, value)