from __future__ import annotations

import os

from adapter.openra_env import OpenRAEnv
from agents.commander import build_commander
from openra_api.game_api import GameAPI
from the_seed.core.errors import ModelInvocationError

def main():
    api = GameAPI(host="127.0.0.1", port=7445, language="zh")
    # if not api.is_server_running():
    #     print("OpenRA API server not reachable.")
    #     return
    env = OpenRAEnv(api)
    runtime = build_commander(env)
    try:
        runtime.run()  # Ctrl+C 结束
    except ModelInvocationError as exc:
        print("[LLM 调用失败]", exc.summary)
        if exc.detail:
            print("详情:", exc.detail)
        if os.environ.get("SEED_SHOW_TRACE") == "1":
            raise

if __name__ == "__main__":
    main()