from __future__ import annotations

from adapter.openra_env import OpenRAEnv
# from inner_loop import InnerLoopRuntime
from openra_api.game_api import GameAPI
from openra_api.models import Location, TargetsQueryParam, Actor,MapQueryResult,FrozenActor,ControlPoint,ControlPointQueryResult,MatchInfoQueryResult,PlayerBaseInfo,ScreenInfoResult

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
    # Start Dashboard Bridge
    DashboardBridge().start(port=8080)
    
    api = GameAPI(host="localhost", port=7445, language="zh")
    
    factory = NodeFactory()
    # inner = InnerLoopRuntime()

    ctx = FSMContext(goal="展开基地车，建造兵营和电厂，然后建造3个步兵")
    fsm = FSM(ctx=ctx)
    bb = fsm.ctx.blackboard
    bb.gameapi = api
    bb.gameapi_rules = build_def_style_prompt(api, ["produce", "wait", "deploy_units"])
    bb.runtime_globals = {"gameapi": api,"api": api,"Location": Location,"TargetsQueryParam": TargetsQueryParam,"Actor": Actor,"MapQueryResult": MapQueryResult,"FrozenActor": FrozenActor,"ControlPoint": ControlPoint,"ControlPointQueryResult": ControlPointQueryResult,"MatchInfoQueryResult": MatchInfoQueryResult,"PlayerBaseInfo": PlayerBaseInfo,"ScreenInfoResult": ScreenInfoResult}

    logger.info("FSM start state=%s", fsm.state)

    # [Todo] 你会把这里接到“慢环”：例如只在触发条件下跑 PLAN/生成/评审/提交
    # 这里仅展示主线推进结构，不保证执行有意义（因为 model backend 未配置）
    for _ in range(15):
        if fsm.state == FSMState.STOP:
            break
        run_fsm_once(fsm, factory)


if __name__ == "__main__":
    main()
