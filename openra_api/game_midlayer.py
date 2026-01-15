from __future__ import annotations

"""对外门面：保持 `python -m openra_api.game_midlayer` 可运行。

实际实现已拆分到多个模块，避免单文件过长。
"""

from .actor_view import ActorView
from .game_api import GameAPI
from .intel.memory import IntelMemory
from .intel.model import IntelModel
from .intel.names import normalize_unit_name
from .intel.serializer import IntelSerializer
from .intel.service import IntelService
from .macro_actions import MacroActions
from .map_accessor import MapAccessor
from .rts_middle_layer import RTSMiddleLayer
from .skill_result import SkillResult
from .models import Actor, Location, MapQueryResult, TargetsQueryParam
from .jobs import ExploreJob, JobManager

if __name__ == "__main__":
    import json
    import time
    
    api = GameAPI("localhost")
    # mid = RTSMiddleLayer(api)
    # # print(api.query_actor(TargetsQueryParam(faction="自己")))
    # # print(mid.intel_service.get_intel(force=True))
    # print(mid.intel())
    
    # print(mid.intel())
    
    # exit()

    if not api.is_server_running("localhost"):
        raise SystemExit("OpenRA 服务器未运行：请先启动游戏并启用 API")

    # 1) 展开基地车（若没有基地车则直接跳过）
    api.deploy_mcv_and_wait(wait_time=1.0)

    # 2) 建造电厂（自动放置建筑）
    # ensure_can_build_wait 只确保前置；真正建造用 produce_wait + auto_place_building=True
    api.ensure_can_build_wait("电厂")
    api.produce_wait("电厂", 1, auto_place_building=True)

    # 3) 生产 3 个步兵（并识别“新生产出来的那 3 个”的 actor_id）
    # 这里会自动确保前置（如兵营），并在需要时先造出前置建筑
    before_infantry = api.query_actor(TargetsQueryParam(type=["步兵"], faction="自己"))
    # before_ids = {int(a.actor_id) for a in before_infantry}

    api.ensure_can_produce_unit("步兵")
    api.produce_wait("步兵", 3, auto_place_building=True)
    time.sleep(0.5)

    after_infantry = api.query_actor(TargetsQueryParam(type=["步兵"], faction="自己"))
    after_ids = {int(a.actor_id) for a in after_infantry}
    new_ids = list(after_ids)

    # 4) 将步兵显式分配为探索地图：job 先创建好，再分配 actor（避免 job 之间争抢）
    intel = IntelService(api)
    mgr = JobManager(api=api, intel=intel)
    explore_job_id = "explore_infantry"
    mgr.add_job(ExploreJob(job_id=explore_job_id))

    # 优先分配新生产的步兵；不足则用离基地最近的步兵补足到 3 个
    snapshot = intel.get_snapshot(force=True)
    base_center = intel.get_base_center(snapshot)
    infantry_by_dist = sorted(
        after_infantry,
        key=lambda a: (getattr(a, "position", None).manhattan_distance(base_center) if getattr(a, "position", None) else 10**9),
    )

    assigned = 0
    used_ids = set()
    for aid in new_ids:
        if mgr.assign_actor_id_to_job(aid, explore_job_id):
            used_ids.add(int(aid))

    # main loop：每 1s tick_jobs()，每 1s 打印 jobs_status
    while True:
        start = time.time()
        mgr.tick_jobs()
        print(json.dumps(mgr.jobs_status(), ensure_ascii=False))
        time.sleep(max(0.0, 1.0 - (time.time() - start)))
