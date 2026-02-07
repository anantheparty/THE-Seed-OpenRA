from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import yaml

from common import load_yaml


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def apply_rollback(runtime_cfg: Dict[str, Any], guardrails: Dict[str, Any]) -> Dict[str, Any]:
    rb = guardrails.get("rollback", {})
    mode = str(rb.get("mode", "disable_gateway"))

    if mode == "shadow_mode":
        runtime_cfg["shadow_mode"] = True
    else:
        runtime_cfg["enabled"] = False
        runtime_cfg["shadow_mode"] = False

    if bool(rb.get("set_rollout_zero", True)):
        rollout = runtime_cfg.setdefault("rollout", {})
        rollout["enabled"] = True
        rollout["default_percentage"] = 0
        by_agent = rollout.get("percentages_by_agent", {})
        if isinstance(by_agent, dict):
            for k in list(by_agent.keys()):
                by_agent[k] = 0
        rollout["percentages_by_agent"] = by_agent

    runtime_cfg["phase"] = "phase4_rollback_active"
    return runtime_cfg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="nlu_pipeline/reports/phase4_metrics.json")
    parser.add_argument("--guardrails", default="nlu_pipeline/configs/phase4_guardrails.yaml")
    parser.add_argument("--runtime-config", default="nlu_pipeline/configs/runtime_gateway.yaml")
    parser.add_argument("--report", default="nlu_pipeline/reports/phase4_rollback_report.json")
    parser.add_argument("--report-md", default="nlu_pipeline/reports/phase4_rollback_report.md")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    metrics = load_json(Path(args.metrics))
    guardrails = load_yaml(Path(args.guardrails))
    runtime_cfg_path = Path(args.runtime_config)
    runtime_cfg = load_yaml(runtime_cfg_path)

    totals = metrics.get("totals", {})
    latency = metrics.get("latency_ms", {})
    window = guardrails.get("window", {})
    thresholds = guardrails.get("thresholds", {})

    events = int(totals.get("events", 0))
    min_events = int(window.get("min_events", 1))
    enough_data = events >= min_events

    checks = [
        {
            "name": "min_route_rate",
            "value": float(totals.get("route_rate", 0.0)),
            "comparator": ">=",
            "threshold": float(thresholds.get("min_route_rate", 0.0)),
        },
        {
            "name": "max_router_unmatched_fallback_rate",
            "value": float(totals.get("router_unmatched_fallback_rate", 0.0)),
            "comparator": "<=",
            "threshold": float(thresholds.get("max_router_unmatched_fallback_rate", 1.0)),
        },
        {
            "name": "max_unknown_route_reason_rate",
            "value": float(totals.get("unknown_route_reason_rate", 0.0)),
            "comparator": "<=",
            "threshold": float(thresholds.get("max_unknown_route_reason_rate", 1.0)),
        },
        {
            "name": "max_suspicious_attack_route_rate",
            "value": float(totals.get("suspicious_attack_route_rate", 0.0)),
            "comparator": "<=",
            "threshold": float(thresholds.get("max_suspicious_attack_route_rate", 1.0)),
        },
        {
            "name": "max_latency_p95_ms",
            "value": float(latency.get("p95", 0.0)),
            "comparator": "<=",
            "threshold": float(thresholds.get("max_latency_p95_ms", 1e9)),
        },
    ]

    breaches: List[Dict[str, Any]] = []
    if enough_data:
        for c in checks:
            if c["comparator"] == ">=":
                passed = c["value"] >= c["threshold"]
            else:
                passed = c["value"] <= c["threshold"]
            if not passed:
                breaches.append({**c, "passed": False})

    triggered = enough_data and bool(breaches)
    applied = False
    mode = "none"
    if triggered:
        mode = str(guardrails.get("rollback", {}).get("mode", "disable_gateway"))
        if not args.dry_run:
            runtime_cfg = apply_rollback(runtime_cfg, guardrails)
            runtime_cfg_path.write_text(
                yaml.safe_dump(runtime_cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            applied = True

    result = {
        "events": events,
        "min_events": min_events,
        "enough_data": enough_data,
        "triggered": triggered,
        "applied": applied,
        "dry_run": bool(args.dry_run),
        "rollback_mode": mode,
        "breaches": breaches,
        "runtime_config": str(runtime_cfg_path),
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Phase4 Auto Rollback Report",
        "",
        f"- events: `{events}` (min `{min_events}`)",
        f"- enough_data: `{enough_data}`",
        f"- triggered: `{triggered}`",
        f"- applied: `{applied}`",
        f"- dry_run: `{bool(args.dry_run)}`",
        f"- rollback_mode: `{mode}`",
        "",
    ]
    if breaches:
        lines.append("## Breaches")
        for b in breaches:
            lines.append(
                f"- {b['name']}: value={float(b['value']):.6f} {b['comparator']} threshold={float(b['threshold']):.6f}"
            )
    else:
        lines.append("## Breaches")
        lines.append("- none")

    Path(args.report_md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"[phase4_auto_rollback] triggered={triggered} applied={applied} dry_run={bool(args.dry_run)} breaches={len(breaches)}"
    )

    if triggered and args.dry_run:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
