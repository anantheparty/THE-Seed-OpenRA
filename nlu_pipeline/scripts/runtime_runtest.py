from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.nlu_gateway import Phase2NLUGateway, logger as gateway_logger
from nlu_pipeline.scripts.common import load_yaml, read_jsonl
from the_seed.core import ExecutionResult

ATTACK_WORD_RE = re.compile(r"(攻击|进攻|突袭|集火|全军出击|打|压上|推过去)")


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def run(self, command: str) -> ExecutionResult:
        self.calls.append(("llm", command))
        return ExecutionResult(success=True, message="LLM fallback executed", code="", observations="")

    def _execute_code(self, code: str) -> ExecutionResult:
        self.calls.append(("route", code[:80]))
        return ExecutionResult(success=True, message="NLU routed executed", code=code, observations="")

    def _record_history(self, command: str, code: str, result: ExecutionResult) -> None:
        self.calls.append(("history", command, result.success))


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int(round((len(vals) - 1) * p))
    idx = max(0, min(len(vals) - 1, idx))
    return float(vals[idx])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="nlu_pipeline/configs/runtime_runtest.yaml")
    parser.add_argument("--out", default="nlu_pipeline/reports/phase6_runtest_report.json")
    parser.add_argument("--out-md", default="nlu_pipeline/reports/phase6_runtest_report.md")
    args = parser.parse_args()

    cfg = load_yaml(Path(args.config))
    ds_cfg = cfg.get("dataset", {})
    gates = cfg.get("gates", {})

    data_path = Path(ds_cfg.get("in", "nlu_pipeline/data/datasets/test.jsonl"))
    max_rows = int(ds_cfg.get("max_rows", 0))
    sample_seed = int(ds_cfg.get("seed", 42))
    rows = read_jsonl(data_path)
    if max_rows > 0 and len(rows) > max_rows:
        rng = random.Random(sample_seed)
        rows = rows[:]
        rng.shuffle(rows)
        rows = rows[:max_rows]

    try:
        gateway_logger.setLevel(logging.WARNING)
    except Exception:
        pass
    gw = Phase2NLUGateway(name="runtime_runtest")
    ex = FakeExecutor()
    status = gw.status()

    total = len(rows)
    route_count = 0
    fallback_count = 0
    low_risk_total = 0
    low_risk_route = 0
    attack_total = 0
    attack_route = 0
    composite_total = 0
    composite_route = 0
    unsafe_composite_route = 0
    route_intent_mismatch = 0
    latency_values: List[float] = []
    by_intent = defaultdict(lambda: {"total": 0, "route": 0, "fallback": 0})
    fallback_reasons = Counter()

    for row in rows:
        text = str(row.get("text", "")).strip()
        expected = str(row.get("intent", "fallback_other"))
        if not text:
            continue

        result, meta = gw.run(ex, text, rollout_key="runtime_runtest")
        _ = result
        source = str(meta.get("source", ""))
        reason = str(meta.get("reason", ""))
        route_intent = str(meta.get("route_intent", "") or "")
        latency = float(meta.get("latency_ms", 0.0) or 0.0)
        latency_values.append(latency)

        by_intent[expected]["total"] += 1
        if source == "nlu_route":
            route_count += 1
            by_intent[expected]["route"] += 1
            if route_intent and route_intent != expected:
                route_intent_mismatch += 1
        else:
            fallback_count += 1
            by_intent[expected]["fallback"] += 1
            fallback_reasons[reason] += 1

        if expected in {"deploy_mcv", "produce", "explore", "mine", "query_actor"}:
            low_risk_total += 1
            if source == "nlu_route":
                low_risk_route += 1
        if expected == "attack":
            attack_total += 1
            if source == "nlu_route":
                attack_route += 1
        if expected == "composite_sequence":
            composite_total += 1
            if source == "nlu_route":
                composite_route += 1
            if source == "nlu_route" and ATTACK_WORD_RE.search(text):
                unsafe_composite_route += 1

    route_rate = route_count / total if total else 0.0
    low_risk_route_rate = low_risk_route / low_risk_total if low_risk_total else 0.0
    attack_route_rate = attack_route / attack_total if attack_total else 0.0
    composite_route_rate = composite_route / composite_total if composite_total else 0.0
    unsafe_composite_route_rate = unsafe_composite_route / composite_route if composite_route else 0.0
    route_intent_mismatch_rate = route_intent_mismatch / route_count if route_count else 0.0
    latency_p95 = percentile(latency_values, 0.95)

    failures: List[str] = []
    if total < int(gates.get("min_events", 0)):
        failures.append(f"events {total} < min_events {int(gates.get('min_events', 0))}")
    if route_rate < float(gates.get("min_route_rate", 0.0)):
        failures.append(
            f"route_rate {route_rate:.4f} < min_route_rate {float(gates.get('min_route_rate', 0.0)):.4f}"
        )
    if low_risk_route_rate < float(gates.get("min_low_risk_route_rate", 0.0)):
        failures.append(
            "low_risk_route_rate "
            f"{low_risk_route_rate:.4f} < min {float(gates.get('min_low_risk_route_rate', 0.0)):.4f}"
        )
    if attack_route_rate < float(gates.get("min_attack_route_rate", 0.0)):
        failures.append(
            f"attack_route_rate {attack_route_rate:.4f} < min {float(gates.get('min_attack_route_rate', 0.0)):.4f}"
        )
    if composite_route_rate < float(gates.get("min_composite_route_rate", 0.0)):
        failures.append(
            "composite_route_rate "
            f"{composite_route_rate:.4f} < min {float(gates.get('min_composite_route_rate', 0.0)):.4f}"
        )
    if unsafe_composite_route_rate > float(gates.get("max_unsafe_composite_route_rate", 1.0)):
        failures.append(
            "unsafe_composite_route_rate "
            f"{unsafe_composite_route_rate:.4f} > max {float(gates.get('max_unsafe_composite_route_rate', 1.0)):.4f}"
        )
    if route_intent_mismatch_rate > float(gates.get("max_route_intent_mismatch_rate", 1.0)):
        failures.append(
            "route_intent_mismatch_rate "
            f"{route_intent_mismatch_rate:.4f} > max {float(gates.get('max_route_intent_mismatch_rate', 1.0)):.4f}"
        )
    if latency_p95 > float(gates.get("max_latency_p95_ms", 1e9)):
        failures.append(
            f"latency_p95 {latency_p95:.2f} > max {float(gates.get('max_latency_p95_ms', 1e9)):.2f}"
        )

    report = {
        "gateway_status": status,
        "totals": {
            "events": total,
            "route_count": route_count,
            "fallback_count": fallback_count,
            "route_rate": route_rate,
            "low_risk_total": low_risk_total,
            "low_risk_route_rate": low_risk_route_rate,
            "attack_total": attack_total,
            "attack_route_rate": attack_route_rate,
            "composite_total": composite_total,
            "composite_route_rate": composite_route_rate,
            "unsafe_composite_route_count": unsafe_composite_route,
            "unsafe_composite_route_rate": unsafe_composite_route_rate,
            "route_intent_mismatch_count": route_intent_mismatch,
            "route_intent_mismatch_rate": route_intent_mismatch_rate,
        },
        "latency_ms": {"p95": latency_p95, "count": len(latency_values)},
        "per_intent": {
            k: {
                **v,
                "route_rate": (v["route"] / v["total"] if v["total"] else 0.0),
            }
            for k, v in sorted(by_intent.items())
        },
        "fallback_reasons_top20": fallback_reasons.most_common(20),
        "failures": failures,
        "passed": len(failures) == 0,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Runtime Run Test Report",
        "",
        f"- result: {'PASS' if report['passed'] else 'FAIL'}",
        f"- events: {total}",
        f"- route_rate: {route_rate:.4f}",
        f"- low_risk_route_rate: {low_risk_route_rate:.4f}",
        f"- attack_route_rate: {attack_route_rate:.4f}",
        f"- composite_route_rate: {composite_route_rate:.4f}",
        f"- unsafe_composite_route_rate: {unsafe_composite_route_rate:.4f}",
        f"- route_intent_mismatch_rate: {route_intent_mismatch_rate:.4f}",
        f"- latency_p95_ms: {latency_p95:.2f}",
        "",
    ]
    if failures:
        lines.append("## Gate Failures")
        for f in failures:
            lines.append(f"- {f}")
    else:
        lines.append("## Gate Check")
        lines.append("- All runtime run-test gates passed")
    Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[runtime_runtest] passed={report['passed']} events={total} route_rate={route_rate:.4f}")
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
