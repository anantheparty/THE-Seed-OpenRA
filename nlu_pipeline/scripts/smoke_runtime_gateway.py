from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.nlu_gateway import Phase2NLUGateway
from the_seed.core import ExecutionResult


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = []

    def run(self, command: str) -> ExecutionResult:
        self.calls.append(("llm", command))
        return ExecutionResult(success=True, message="LLM fallback executed", code="", observations="")

    def _execute_code(self, code: str) -> ExecutionResult:
        if "api.dispatch_explore(" in code:
            self.calls.append(("route_fail", code[:80]))
            return ExecutionResult(success=False, message="执行失败: 未找到可侦察单位", code=code, observations="")
        self.calls.append(("route", code[:80]))
        return ExecutionResult(success=True, message="NLU routed executed", code=code, observations="")

    def _record_history(self, command: str, code: str, result: ExecutionResult) -> None:
        self.calls.append(("history", command, result.success))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="nlu_pipeline/reports/runtime_gateway_smoke.json")
    args = parser.parse_args()

    gateway = Phase2NLUGateway(name="smoke")
    ex = FakeExecutor()

    cases = [
        {"text": "造两个步兵", "expect": "route_or_fallback"},
        {"text": "展开基地车", "expect": "route_or_fallback"},
        {"text": "先展开基地车然后造两个步兵", "expect": "route"},
        {"text": "打开设置", "expect": "fallback"},
        {"text": "停止攻击", "expect": "route_or_fallback"},
        {"text": "用坦克攻击敌方矿车", "expect": "route"},
        {"text": "全面进攻", "expect": "route"},
        {"text": "探索地图", "expect": "fallback"},
        {"text": "先造两个步兵然后进攻敌方矿车", "expect": "fallback"},
        {"text": "我想打个招呼", "expect": "fallback"},
        {"text": "全军出击", "expect": "route"},
    ]

    rows = []
    route_count = 0
    fallback_count = 0
    failures = []

    for item in cases:
        result, meta = gateway.run(ex, item["text"])
        source = meta.get("source")
        if source == "nlu_route":
            route_count += 1
        else:
            fallback_count += 1

        if item["expect"] == "fallback" and source != "llm_fallback":
            failures.append(f"expected fallback but got {source} for: {item['text']}")
        if item["expect"] == "route" and source != "nlu_route":
            failures.append(f"expected route but got {source} for: {item['text']}")

        rows.append(
            {
                "text": item["text"],
                "source": source,
                "reason": meta.get("reason"),
                "intent": meta.get("intent"),
                "confidence": meta.get("confidence"),
                "success": result.success,
            }
        )

    # Phase6 readiness requires attack + safe composite routes.
    if gateway.is_enabled() and route_count < 3:
        failures.append("gateway active but route_count < 3")
    if gateway.is_enabled() and not any(
        c["text"] == "用坦克攻击敌方矿车" and c["source"] == "nlu_route" for c in rows
    ):
        failures.append("attack route not enabled for explicit attack command")
    if gateway.is_enabled() and not any(
        c["text"] == "全面进攻" and c["source"] == "nlu_route" for c in rows
    ):
        failures.append("global attack route not enabled for 全面进攻")
    if gateway.is_enabled() and not any(
        c["text"] == "探索地图"
        and c["source"] == "llm_fallback"
        and str(c["reason"]).startswith("nlu_route_exec_failed:")
        for c in rows
    ):
        failures.append("route-exec-failed fallback not enabled for 探索地图")
    if gateway.is_enabled() and not any(
        c["text"] == "停止攻击" and c["source"] == "nlu_route" for c in rows
    ):
        failures.append("stop_attack direct route not enabled")
    if gateway.is_enabled() and not any(
        c["text"] == "先展开基地车然后造两个步兵" and c["source"] == "nlu_route" for c in rows
    ):
        failures.append("phase6 composite route not enabled for safe composite command")
    if gateway.is_enabled() and any(
        c["text"] == "先造两个步兵然后进攻敌方矿车" and c["source"] == "nlu_route" for c in rows
    ):
        failures.append("unsafe composite with attack step should fallback")

    report = {
        "gateway_status": gateway.status(),
        "route_count": route_count,
        "fallback_count": fallback_count,
        "cases": rows,
        "failures": failures,
        "passed": len(failures) == 0,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[smoke_runtime_gateway] passed={report['passed']} route_count={route_count} "
        f"fallback_count={fallback_count}"
    )
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
