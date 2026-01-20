from __future__ import annotations
from typing import Any, Dict, List, Optional
import logging

from adapter.openra_env import OpenRAEnv
from openra_api.game_api import GameAPI
from openra_api.game_midlayer import RTSMiddleLayer
from openra_api.models import Location, TargetsQueryParam, Actor, MapQueryResult, FrozenActor, ControlPoint, ControlPointQueryResult, MatchInfoQueryResult, PlayerBaseInfo, ScreenInfoResult

from the_seed.core.factory import NodeFactory
from the_seed.core.fsm import FSM, FSMContext, FSMState
from the_seed.utils import build_def_style_prompt, DashboardBridge, LogManager

from agents.global_blackboard import GlobalBlackboard, Signal

logger = LogManager.get_logger()

class BaseAgent:
    """
    智能体基类，封装了 FSM 和与 GlobalBlackboard 的交互。
    遵循 'Wrapper' 模式，不修改 the-seed 核心代码。
    """

    def __init__(
        self, 
        name: str, 
        global_bb: GlobalBlackboard, 
        game_api: GameAPI, 
        mid_layer: RTSMiddleLayer
    ):
        self.name = name
        self.global_bb = global_bb
        self.game_api = game_api
        self.mid_layer = mid_layer

        # 初始化 FSM 组件
        self.factory = NodeFactory()
        self.ctx = FSMContext(goal="")
        self.fsm = FSM(ctx=self.ctx)
        
        # 初始化 Local Blackboard
        self._setup_blackboard()
        
        # 观测环境包装器 (持久化以复用内部缓存，如果 OpenRAEnv 支持的话)
        # 注意：main.py 中是每次 tick 创建，这里改为持久化尝试优化，
        # 如果发现状态不更新问题，可回退为 tick 中创建。
        self.env = OpenRAEnv(self.game_api)

        # 注册到全局黑板
        self.global_bb.registered_agents[self.name] = "INIT"
        logger.info(f"Agent [{self.name}] initialized.")

    def _setup_blackboard(self):
        """配置本地黑板环境，注入 API 和运行时全局变量"""
        bb = self.fsm.ctx.blackboard
        
        # 注入 API 句柄
        bb.gameapi = self.game_api
        bb.midapi = self.mid_layer.skills
        
        # 注入 Agent 身份信息 (供 Prompt 使用)
        bb.agent_name = self.name
        
        # 生成动态 API 文档 (Prompt)
        # TODO: 未来可以根据 Agent 角色裁剪可用函数列表
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
            title=f"Available functions for {self.name} (MacroActions):",
            include_doc_first_line=True,
            include_doc_block=False,
        )

        # 注入 Python 执行环境全局变量
        bb.runtime_globals = {
            "gameapi": bb.midapi,
            "api": bb.midapi,
            "raw_api": self.game_api,
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
            "send_signal": self.send_signal, # Expose signal sending to LLM
        }

    def send_signal(self, receiver: str, type: str, payload: Any = None):
        """发送信号到全局黑板 (供 LLM 调用)"""
        sig = Signal(sender=self.name, receiver=receiver, type=type, payload=payload)
        self.global_bb.publish_signal(sig)
        logger.info(f"[{self.name}] Sent signal: {type} to {receiver}")

    def tick(self) -> None:
        """
        执行一次主循环：感知 -> 决策 -> 执行 -> 反馈
        """
        # 0. 状态检查
        if self.fsm.state == FSMState.STOP:
            return

        # 1. 同步 Global -> Local
        self._sync_global_to_local()

        # 2. 执行 FSM Step
        try:
            self._run_fsm_once()
        except Exception as e:
            logger.error(f"Agent [{self.name}] FSM error: {e}", exc_info=True)
            # 可以选择在这里处理错误，例如重置状态
            
        # 3. 同步 Local -> Global
        self._sync_local_to_global()

        # 4. 更新 Dashboard
        # 注意：DashboardBridge 目前可能只支持单例或默认 socket。
        # 如果需要多 Agent 显示，Dashboard 端也需要改造。
        # 暂时只让 "Adjutant" 或主 Agent 推送状态，或者轮流推送（会闪烁）。
        # 这里先注释掉自动推送，交由 Main Loop 决定谁来推，或者修改 Bridge 支持多实例。
        # DashboardBridge().update_fsm_state(self.fsm) 

    def _run_fsm_once(self):
        node = self.factory.get_node(self.fsm.state)
        bb = self.fsm.ctx.blackboard
        
        # 感知
        # TODO: 以后这里可以根据 Agent 角色只获取部分感知
        bb.game_basic_state = str(self.env.observe())
        
        # 运行节点
        out = node.run(self.fsm)
        
        # 状态流转
        self.fsm.transition(out.next_state)
        
        # 更新状态记录
        self.global_bb.registered_agents[self.name] = str(self.fsm.state)

    def _sync_global_to_local(self):
        """从全局黑板获取指令和信号"""
        # 示例：如果是副官 (Adjutant)，直接响应用户指令
        if self.name == "Adjutant":
            if self.global_bb.command and self.global_bb.command != self.fsm.ctx.goal:
                logger.info(f"[{self.name}] Received new command: {self.global_bb.command}")
                self.fsm.ctx.goal = self.global_bb.command
                # 重置 FSM 状态以开始新任务？视情况而定
                # self.fsm.transition(FSMState.OBSERVE) 

        # 处理信号
        signals = self.global_bb.consume_signals(self.name)
        for sig in signals:
            logger.info(f"[{self.name}] Received signal: {sig.type} from {sig.sender}")
            # TODO: 将信号内容注入到 Local Blackboard 的 events 或 memory 中
            if "events" not in self.fsm.ctx.blackboard:
                self.fsm.ctx.blackboard.events = []
            self.fsm.ctx.blackboard.events.append(f"Signal received: {sig.type} payload={sig.payload}")

    def _sync_local_to_global(self):
        """将本地状态或产出同步回全局"""
        # 示例：将执行结果摘要写入 Market 或 Log
        pass
