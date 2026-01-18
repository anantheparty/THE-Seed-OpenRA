from __future__ import annotations

import json
import time

from .game_api import GameAPI
from .intel.service import IntelService
from .jobs import AttackJob, ExploreJob, JobManager
from .actor_utils import select_combat_units, select_scouts


def main() -> None:
    api = GameAPI("localhost")
    if not api.is_server_running("localhost"):
        raise SystemExit("OpenRA 服务器未运行：请先启动游戏并启用 API")

    intel = IntelService(api)
    mgr = JobManager(api=api, intel=intel)

    # 长期意图：探索 + 攻击
    mgr.add_job(ExploreJob(job_id="explore", radius=28))
    mgr.add_job(AttackJob(job_id="attack", step=8))

    # 显式分配：job 创建好后，再把 actor 分给 job（避免 job 之间争抢）
    snapshot = intel.get_snapshot(force=True)
    my_actors = snapshot.get("my_actors", [])
    scouts = select_scouts(my_actors, max_scouts=1)
    combat = select_combat_units(my_actors)[:8]
    for a in scouts:
        mgr.assign_actor_to_job(a, "explore")
    for a in combat:
        # 避免与 explore 重叠
        if int(getattr(a, "actor_id", getattr(a, "id", -1))) in mgr.actor_job:
            continue
        mgr.assign_actor_to_job(a, "attack")

    # 简单 main loop：每 1s tick_jobs()，每 1s 打印 jobs_status
    while True:
        start = time.time()
        mgr.tick_jobs()
        print(json.dumps(mgr.jobs_status(), ensure_ascii=False))
        elapsed = time.time() - start
        time.sleep(max(0.0, 1.0 - elapsed))


if __name__ == "__main__":
    main()


