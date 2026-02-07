from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

from common import norm_text, read_jsonl, text_id, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", default="nlu_pipeline/data/raw/logs/commands_from_logs.jsonl")
    parser.add_argument("--web", default="nlu_pipeline/data/raw/web/commands_from_web.jsonl")
    parser.add_argument("--synth", default="nlu_pipeline/data/raw/synthetic/commands_synth.jsonl")
    parser.add_argument("--online", default="nlu_pipeline/data/raw/online/nlu_decisions.jsonl")
    parser.add_argument("--phase43", default="nlu_pipeline/data/raw/phase4/commands_phase43_batch.jsonl")
    parser.add_argument("--out", default="nlu_pipeline/data/interim/unlabeled_pool.jsonl")
    args = parser.parse_args()

    phase43_rows = read_jsonl(Path(args.phase43))
    rows: List[Dict] = []
    if phase43_rows:
        # Phase4.3 curated batch is the primary source for product training data.
        rows.extend(phase43_rows)
    else:
        for p in [args.logs, args.web, args.synth]:
            rows.extend(read_jsonl(Path(p)))
    for row in read_jsonl(Path(args.online)):
        text = str(row.get("command", "")).strip()
        if not text:
            continue
        rows.append(
            {
                "id": row.get("id"),
                "text": text,
                "source": "online_decision",
                "intent": row.get("intent"),
                "slots": {},
                "risk_level": row.get("risk_level"),
                "label_source": "runtime_decision",
                "nlu_source": row.get("source"),
                "nlu_reason": row.get("reason"),
            }
        )

    dedup: Dict[str, Dict] = {}
    for row in rows:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        key = norm_text(text)
        if not key:
            continue
        base = {
            "id": row.get("id") or text_id(text),
            "text": text,
            "source": row.get("source", "unknown"),
            "intent": row.get("intent"),
            "slots": row.get("slots", {}),
            "risk_level": row.get("risk_level"),
            "label_source": row.get("label_source"),
            "meta": {k: v for k, v in row.items() if k not in {"id", "text", "source", "intent", "slots", "risk_level", "label_source"}},
        }

        prev = dedup.get(key)
        if prev is None:
            dedup[key] = base
            continue

        # Keep explicit labels over unlabeled rows
        if prev.get("intent") is None and base.get("intent") is not None:
            dedup[key] = base

    out_rows = list(dedup.values())
    write_jsonl(Path(args.out), out_rows)
    print(f"[build_unlabeled_pool] in={len(rows)} dedup={len(out_rows)} out={args.out}")


if __name__ == "__main__":
    main()
