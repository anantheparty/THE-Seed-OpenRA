from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from common import load_yaml, norm_text, read_jsonl, split_by_ratio, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prelabels", default="nlu_pipeline/data/labeled/prelabels.jsonl")
    parser.add_argument("--manual", default="nlu_pipeline/data/manual/manual_gold_seed.jsonl")
    parser.add_argument("--out-dir", default="nlu_pipeline/data/datasets")
    parser.add_argument("--report", default="nlu_pipeline/reports/dataset_report.json")
    args = parser.parse_args()

    cfg = load_yaml(Path("nlu_pipeline/configs/pipeline.yaml"))
    schema = load_yaml(Path("nlu_pipeline/configs/label_schema.yaml"))
    allowed_intents = set(schema.get("intents", []))

    silver = read_jsonl(Path(args.prelabels))
    manual = read_jsonl(Path(args.manual))

    manual_map = {norm_text(str(r.get("text", ""))): r for r in manual if r.get("intent")}

    merged: List[Dict[str, Any]] = []
    for row in silver:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        key = norm_text(text)
        if key in manual_map:
            m = manual_map[key]
            row["intent"] = m.get("intent")
            row["slots"] = m.get("slots", {})
            row["risk_level"] = m.get("risk_level", "low")
            row["label_source"] = "manual_gold_override"
            row["confidence"] = 1.0
        if row.get("intent") not in allowed_intents:
            row["intent"] = "fallback_other"
            row["slots"] = {}
            row["risk_level"] = "low"
        merged.append(row)

    # Include manual-only rows not present in silver set
    silver_keys = {norm_text(str(r.get("text", ""))) for r in merged}
    for m in manual:
        t = str(m.get("text", "")).strip()
        if not t:
            continue
        k = norm_text(t)
        if k in silver_keys:
            continue
        merged.append(
            {
                "id": m.get("id"),
                "text": t,
                "source": "manual",
                "intent": m.get("intent", "fallback_other"),
                "slots": m.get("slots", {}),
                "risk_level": m.get("risk_level", "low"),
                "label_source": "manual_gold",
                "confidence": 1.0,
            }
        )

    by_intent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in merged:
        by_intent[str(row["intent"])].append(row)

    split_cfg = cfg.get("split_ratio", {})
    train_ratio = float(split_cfg.get("train", 0.7))
    dev_ratio = float(split_cfg.get("dev", 0.15))
    seed = int(cfg.get("random_seed", 42))

    train_rows: List[Dict[str, Any]] = []
    dev_rows: List[Dict[str, Any]] = []
    test_rows: List[Dict[str, Any]] = []

    for intent, rows in by_intent.items():
        part = split_by_ratio(rows, train=train_ratio, dev=dev_ratio, seed=seed)
        train_rows.extend(part["train"])
        dev_rows.extend(part["dev"])
        test_rows.extend(part["test"])

    out_dir = Path(args.out_dir)
    write_jsonl(out_dir / "train.jsonl", train_rows)
    write_jsonl(out_dir / "dev.jsonl", dev_rows)
    write_jsonl(out_dir / "test.jsonl", test_rows)

    report = {
        "total": len(merged),
        "train": len(train_rows),
        "dev": len(dev_rows),
        "test": len(test_rows),
        "distribution": {k: len(v) for k, v in sorted(by_intent.items())},
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[build_dataset] total={report['total']} train={report['train']} "
        f"dev={report['dev']} test={report['test']}"
    )


if __name__ == "__main__":
    main()
