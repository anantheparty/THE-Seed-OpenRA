from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.nlu_gateway import Phase2NLUGateway
from the_seed.core import ExecutionResult


@dataclass
class Case:
    text: str
    expected_intent: str
    expect_route: bool = True


class FakeExecutor:
    def run(self, command: str) -> ExecutionResult:
        return ExecutionResult(success=True, message=f"fallback:{command}", code="", observations="")

    def _execute_code(self, code: str) -> ExecutionResult:
        return ExecutionResult(success=True, message="route", code=code, observations="")

    def _record_history(self, command: str, code: str, result: ExecutionResult) -> None:
        _ = command, code, result


def build_cases() -> List[Case]:
    return [
        # P0: stop_attack should not be blocked by safety regex.
        Case("停止攻击", "stop_attack"),
        Case("停止进攻", "stop_attack"),
        Case("取消攻击", "stop_attack"),
        Case("停火", "stop_attack"),
        Case("别攻击了先停一下", "stop_attack"),
        Case("不要攻击，停一停", "stop_attack"),
        # P0: polite tail/fillers should not break routing.
        Case("建造一个电厂，快点谢谢", "produce"),
        Case("请帮我造两个步兵，谢谢", "produce"),
        Case("先造两个步兵然后侦察一下，谢谢", "composite_sequence"),
        Case("查询单位敌方战车工厂，快点", "query_actor"),
        Case("查看兵力友军步兵吧", "query_actor"),
        # P1: attack phrase expansion.
        Case("派所有重坦进攻敌方基地", "attack"),
        Case("让步兵突袭敌方矿车", "attack"),
        Case("命令坦克进攻敌方雷达站", "attack"),
        Case("重坦集火敌方矿车", "attack"),
        Case("火箭兵冲上去打敌方矿场", "attack"),
        # P1: query/produce disambiguation.
        Case("列出单位自己V2火箭车", "query_actor"),
        Case("请列出单位我方雷达站", "query_actor"),
        Case("现在列出单位对面防空车", "query_actor"),
        Case("马上制造3辆防空车", "produce"),
        Case("建造2座兵营", "produce"),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="nlu_pipeline/reports/p0_p1_regression_report.json")
    parser.add_argument("--out-md", default="nlu_pipeline/reports/p0_p1_regression_report.md")
    args = parser.parse_args()

    gw = Phase2NLUGateway(name="p0_p1_regression")
    ex = FakeExecutor()
    cases = build_cases()

    failures: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    route_count = 0

    for c in cases:
        result, meta = gw.run(ex, c.text, rollout_key="p0_p1_regression")
        _ = result
        source = str(meta.get("source", ""))
        routed = source == "nlu_route"
        route_intent = str(meta.get("route_intent", "") or "")
        reason = str(meta.get("reason", ""))
        passed = True
        if c.expect_route and not routed:
            passed = False
        if c.expect_route and routed and route_intent != c.expected_intent:
            passed = False
        if not c.expect_route and routed:
            passed = False
        if routed:
            route_count += 1

        row = {
            "text": c.text,
            "expected_intent": c.expected_intent,
            "expect_route": c.expect_route,
            "passed": passed,
            "source": source,
            "reason": reason,
            "intent": meta.get("intent"),
            "route_intent": route_intent,
            "confidence": meta.get("confidence"),
        }
        rows.append(row)
        if not passed:
            failures.append(row)

    report = {
        "total": len(cases),
        "passed": len(failures) == 0,
        "passed_count": len(cases) - len(failures),
        "failed_count": len(failures),
        "route_count": route_count,
        "route_rate": (route_count / len(cases) if cases else 0.0),
        "failures": failures,
        "rows": rows,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# P0/P1 Regression Report",
        "",
        f"- total: {report['total']}",
        f"- passed: {report['passed']}",
        f"- passed_count: {report['passed_count']}",
        f"- failed_count: {report['failed_count']}",
        f"- route_rate: {report['route_rate']:.4f}",
        "",
    ]
    if failures:
        lines.append("## Failures")
        for f in failures:
            lines.append(
                f"- {f['text']} | expected={f['expected_intent']} source={f['source']} "
                f"route_intent={f['route_intent']} reason={f['reason']}"
            )
    else:
        lines.append("## Result")
        lines.append("- All curated P0/P1 regression cases passed")

    Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        "[p0_p1_regression] "
        f"passed={report['passed']} total={report['total']} failed={report['failed_count']} route_rate={report['route_rate']:.4f}"
    )
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
