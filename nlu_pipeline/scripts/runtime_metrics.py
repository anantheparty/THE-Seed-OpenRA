from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

from common import PROJECT_ROOT, load_yaml, read_jsonl

ATTACK_WORD_RE = re.compile(r"(攻击|进攻|突袭|集火|全军出击|打|压上|推过去)")


def percentile(values: Iterable[float], p: float) -> float:
    arr = sorted(float(v) for v in values if v is not None)
    if not arr:
        return 0.0
    if len(arr) == 1:
        return arr[0]
    idx = int(round((len(arr) - 1) * p))
    idx = max(0, min(len(arr) - 1, idx))
    return float(arr[idx])


def now_ms() -> int:
    return int(time.time() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="nlu_pipeline/configs/runtime_gateway.yaml")
    parser.add_argument("--guardrails", default="nlu_pipeline/configs/runtime_guardrails.yaml")
    parser.add_argument("--in", dest="in_path", default="")
    parser.add_argument("--out", default="nlu_pipeline/reports/phase4_metrics.json")
    parser.add_argument("--out-md", default="nlu_pipeline/reports/phase4_metrics.md")
    args = parser.parse_args()

    runtime_cfg = load_yaml(Path(args.config))
    guard_cfg = load_yaml(Path(args.guardrails))

    in_path = str(args.in_path).strip()
    if not in_path:
        in_path = str(runtime_cfg.get("online_collection", {}).get("decision_log_path", "")).strip()

    rows = read_jsonl(Path(in_path)) if in_path else []
    window_cfg = guard_cfg.get("window", {})
    max_events = int(window_cfg.get("max_events", 5000))
    max_age_hours = float(window_cfg.get("max_age_hours", 24))
    min_ts = now_ms() - int(max_age_hours * 3600 * 1000)

    filtered: List[Dict[str, Any]] = []
    for row in rows:
        ts = int(row.get("timestamp", 0) or 0)
        if ts and ts < min_ts:
            continue
        filtered.append(row)
    if max_events > 0 and len(filtered) > max_events:
        filtered = filtered[-max_events:]

    allowed_route_reasons = set(str(x) for x in guard_cfg.get("allowed_route_reasons", []))
    total = len(filtered)
    route_count = 0
    fallback_count = 0
    attack_command_count = 0
    attack_route_count = 0
    suspicious_attack_route_count = 0
    unknown_route_reason_count = 0
    router_unmatched_fallback_count = 0
    blocked_by_safety_count = 0
    rollout_holdback_count = 0
    latency_values: List[float] = []
    per_agent = defaultdict(lambda: {"events": 0, "route": 0, "fallback": 0, "attack_route": 0})

    for row in filtered:
        source = str(row.get("source", ""))
        reason = str(row.get("reason", ""))
        command = str(row.get("command", ""))
        agent = str(row.get("agent", "unknown"))
        route_intent = str(row.get("route_intent", "") or "")
        latency_ms = row.get("latency_ms")
        if latency_ms is not None:
            try:
                latency_values.append(float(latency_ms))
            except (TypeError, ValueError):
                pass

        per_agent[agent]["events"] += 1

        has_attack_word = bool(ATTACK_WORD_RE.search(command))
        if has_attack_word:
            attack_command_count += 1

        if source == "nlu_route":
            route_count += 1
            per_agent[agent]["route"] += 1
            if route_intent == "attack" or has_attack_word:
                attack_route_count += 1
                per_agent[agent]["attack_route"] += 1
                # Attack route should always carry explicit attack wording.
                if not has_attack_word:
                    suspicious_attack_route_count += 1

            if allowed_route_reasons and reason not in allowed_route_reasons:
                unknown_route_reason_count += 1
        else:
            fallback_count += 1
            per_agent[agent]["fallback"] += 1
            if reason.startswith("router_unmatched:"):
                router_unmatched_fallback_count += 1
            if reason == "blocked_by_safety_pattern":
                blocked_by_safety_count += 1
            if reason.startswith("rollout_holdback:") or reason.startswith("rollout_zero_"):
                rollout_holdback_count += 1

    route_rate = route_count / total if total else 0.0
    fallback_rate = fallback_count / total if total else 0.0
    attack_route_rate = attack_route_count / attack_command_count if attack_command_count else 0.0
    suspicious_attack_route_rate = (
        suspicious_attack_route_count / attack_route_count if attack_route_count else 0.0
    )
    unknown_route_reason_rate = unknown_route_reason_count / route_count if route_count else 0.0
    router_unmatched_fallback_rate = (
        router_unmatched_fallback_count / fallback_count if fallback_count else 0.0
    )

    per_agent_out: Dict[str, Any] = {}
    for agent, stats in per_agent.items():
        events = int(stats["events"])
        routes = int(stats["route"])
        per_agent_out[agent] = {
            "events": events,
            "route_count": routes,
            "fallback_count": int(stats["fallback"]),
            "attack_route_count": int(stats["attack_route"]),
            "route_rate": routes / events if events else 0.0,
        }

    report: Dict[str, Any] = {
        "phase": runtime_cfg.get("phase", ""),
        "input_path": in_path,
        "window": {
            "max_age_hours": max_age_hours,
            "max_events": max_events,
            "row_count_raw": len(rows),
            "row_count_windowed": total,
        },
        "totals": {
            "events": total,
            "route_count": route_count,
            "fallback_count": fallback_count,
            "route_rate": route_rate,
            "fallback_rate": fallback_rate,
            "attack_command_count": attack_command_count,
            "attack_route_count": attack_route_count,
            "attack_route_rate": attack_route_rate,
            "suspicious_attack_route_count": suspicious_attack_route_count,
            "suspicious_attack_route_rate": suspicious_attack_route_rate,
            "unknown_route_reason_count": unknown_route_reason_count,
            "unknown_route_reason_rate": unknown_route_reason_rate,
            "router_unmatched_fallback_count": router_unmatched_fallback_count,
            "router_unmatched_fallback_rate": router_unmatched_fallback_rate,
            "blocked_by_safety_count": blocked_by_safety_count,
            "rollout_holdback_count": rollout_holdback_count,
        },
        "latency_ms": {
            "count": len(latency_values),
            "p50": percentile(latency_values, 0.50),
            "p95": percentile(latency_values, 0.95),
            "max": max(latency_values) if latency_values else 0.0,
        },
        "per_agent": per_agent_out,
        "generated_at_ms": now_ms(),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Runtime Metrics",
        "",
        f"- phase: `{report['phase']}`",
        f"- events(windowed): `{total}` / raw `{len(rows)}`",
        f"- route_rate: `{route_rate:.4f}`",
        f"- fallback_rate: `{fallback_rate:.4f}`",
        f"- attack_route_rate: `{attack_route_rate:.4f}` ({attack_route_count}/{attack_command_count})",
        (
            f"- suspicious_attack_route_rate: `{suspicious_attack_route_rate:.4f}` "
            f"({suspicious_attack_route_count}/{attack_route_count})"
        ),
        (
            f"- unknown_route_reason_rate: `{unknown_route_reason_rate:.4f}` "
            f"({unknown_route_reason_count}/{route_count})"
        ),
        (
            f"- router_unmatched_fallback_rate: `{router_unmatched_fallback_rate:.4f}` "
            f"({router_unmatched_fallback_count}/{fallback_count})"
        ),
        f"- latency_p95_ms: `{report['latency_ms']['p95']:.2f}`",
        "",
    ]

    if per_agent_out:
        lines.append("## Per Agent")
        for agent, stats in sorted(per_agent_out.items()):
            lines.append(
                f"- {agent}: events={stats['events']} route={stats['route_count']} "
                f"fallback={stats['fallback_count']} route_rate={stats['route_rate']:.4f}"
            )

    Path(args.out_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[runtime_metrics] events={total} route_rate={route_rate:.4f} p95={report['latency_ms']['p95']:.2f}")


if __name__ == "__main__":
    main()
