from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from common import load_yaml, norm_text, read_jsonl, text_id, write_jsonl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nlu_pipeline.runtime import PortableIntentModel

THE_SEED_PATH = PROJECT_ROOT / "the-seed"
if str(THE_SEED_PATH) not in sys.path:
    sys.path.insert(0, str(THE_SEED_PATH))
from the_seed.demos.openra.rules.command_router import CommandRouter  # type: ignore


def has_attack_word(text: str) -> bool:
    return bool(re.search(r"(攻击|进攻|突袭|集火|全军出击|打|压上|推过去)", text))


def select_top(rows: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    rows = sorted(rows, key=lambda x: (-float(x.get("priority", 0.0)), -int(x.get("freq", 1))))
    return rows[:n]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default="nlu_pipeline/data/raw/logs/commands_from_logs.jsonl")
    parser.add_argument(
        "--online-events",
        default="nlu_pipeline/data/raw/online/nlu_decisions.jsonl",
    )
    parser.add_argument("--out", default="nlu_pipeline/data/manual/annotation_queue_phase3.jsonl")
    parser.add_argument("--report", default="nlu_pipeline/reports/annotation_queue_phase3_report.json")
    parser.add_argument("--runtime-model", default="nlu_pipeline/artifacts/intent_model_runtime.json")
    parser.add_argument("--config", default="nlu_pipeline/configs/data_collection.yaml")
    args = parser.parse_args()

    cfg = load_yaml(Path(args.config))
    q_size = int(cfg.get("annotation_queue_size", 2000))
    sampling = cfg.get("sampling", {})
    thresholds = cfg.get("thresholds", {})
    low_conf_th = float(thresholds.get("low_conf", 0.80))

    rows = read_jsonl(Path(args.in_path))
    online_rows = read_jsonl(Path(args.online_events))
    router = CommandRouter()
    model = PortableIntentModel.load(Path(args.runtime_model))

    agg: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        text = str(row.get("text", "")).strip()
        if len(text) < 2 or len(text) > 80:
            continue
        key = norm_text(text)
        if not key:
            continue
        rec = agg.get(key)
        if rec is None:
            pred = model.predict_one(text)
            route = router.route(text)
            route_intent = route.intent if route.matched else "fallback_other"
            rec = {
                "id": text_id(text),
                "text": text,
                "freq": 0,
                "pred_intent": pred.intent,
                "pred_confidence": float(pred.confidence),
                "route_intent": route_intent,
                "route_score": float(route.score or 0.0),
                "route_matched": bool(route.matched),
                "attack_word": has_attack_word(text),
            }
            agg[key] = rec
        rec["freq"] += 1

    # Merge online runtime events to increase signal for active ambiguous/risky commands.
    for row in online_rows:
        text = str(row.get("command", "")).strip()
        if len(text) < 2 or len(text) > 80:
            continue
        key = norm_text(text)
        if not key:
            continue
        rec = agg.get(key)
        if rec is None:
            pred = model.predict_one(text)
            route = router.route(text)
            route_intent = route.intent if route.matched else "fallback_other"
            rec = {
                "id": text_id(text),
                "text": text,
                "freq": 0,
                "pred_intent": pred.intent,
                "pred_confidence": float(pred.confidence),
                "route_intent": route_intent,
                "route_score": float(route.score or 0.0),
                "route_matched": bool(route.matched),
                "attack_word": has_attack_word(text),
            }
            agg[key] = rec
        rec["freq"] += 2
        if str(row.get("source", "")) == "llm_fallback":
            rec["freq"] += 1

    candidates = list(agg.values())

    groups = defaultdict(list)
    for c in candidates:
        pred_intent = c["pred_intent"]
        pred_conf = float(c["pred_confidence"])
        route_intent = c["route_intent"]

        c["priority"] = pred_conf
        if pred_intent == "attack" or c["attack_word"]:
            c["priority"] = max(c["priority"], 1.2)
            groups["high_risk"].append(c)
        if pred_conf < low_conf_th:
            c["priority"] = max(c["priority"], 1.0 - pred_conf + 0.7)
            groups["low_conf"].append(c)
        if pred_intent != route_intent:
            c["priority"] = max(c["priority"], 1.0)
            groups["mismatch"].append(c)
        if not c["route_matched"]:
            c["priority"] = max(c["priority"], 0.95)
            groups["fallback"].append(c)

    selected: Dict[str, Dict[str, Any]] = {}

    def take(group_name: str, n: int) -> None:
        for item in select_top(groups[group_name], n):
            selected[item["id"]] = item

    take("high_risk", int(sampling.get("high_risk_min", 500)))
    take("low_conf", int(sampling.get("low_conf_min", 500)))
    take("mismatch", int(sampling.get("mismatch_min", 500)))
    take("fallback", int(sampling.get("fallback_min", 300)))

    if len(selected) < q_size:
        for item in select_top(candidates, q_size):
            selected[item["id"]] = item
            if len(selected) >= q_size:
                break

    out_rows = list(selected.values())[:q_size]
    write_jsonl(Path(args.out), out_rows)

    report = {
        "input_events": len(rows),
        "online_events": len(online_rows),
        "unique_texts": len(candidates),
        "queue_size": len(out_rows),
        "group_sizes": {k: len(v) for k, v in groups.items()},
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[build_annotation_queue_phase3] input_events={len(rows)} unique={len(candidates)} queue={len(out_rows)}"
    )


if __name__ == "__main__":
    main()
