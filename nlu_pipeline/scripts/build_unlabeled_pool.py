from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

from common import load_yaml, norm_text, read_jsonl, text_id, write_jsonl


def row_score(row: Dict[str, Any]) -> float:
    score = 0.0
    intent = row.get("intent")
    if intent:
        score += 100.0
    conf = row.get("confidence")
    try:
        score += max(0.0, min(float(conf or 0.0), 1.0)) * 10.0
    except (TypeError, ValueError):
        pass
    label_source = str(row.get("label_source", ""))
    if label_source.startswith("dashboard_nlu_route"):
        score += 8.0
    elif label_source.startswith("runtime_decision_route"):
        score += 7.0
    elif label_source == "hf_open_corpus":
        score += 4.0
    source = str(row.get("source", ""))
    if source in {"online_decision", "online_interaction"}:
        score += 2.0
    return score


def make_base_row(row: Dict[str, Any], *, default_source: str) -> Dict[str, Any]:
    text = str(row.get("text", "")).strip()
    return {
        "id": row.get("id") or text_id(text),
        "text": text,
        "source": row.get("source", default_source),
        "intent": row.get("intent"),
        "slots": row.get("slots", {}),
        "risk_level": row.get("risk_level"),
        "label_source": row.get("label_source"),
        "confidence": row.get("confidence"),
        "meta": {
            k: v
            for k, v in row.items()
            if k not in {"id", "text", "source", "intent", "slots", "risk_level", "label_source", "confidence"}
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", default="nlu_pipeline/data/raw/logs/commands_from_logs.jsonl")
    parser.add_argument("--web", default="nlu_pipeline/data/raw/web/commands_from_web.jsonl")
    parser.add_argument("--hf-dialogue", default="nlu_pipeline/data/raw/web/commands_from_hf_dialogue.jsonl")
    parser.add_argument("--synth", default="nlu_pipeline/data/raw/synthetic/commands_synth.jsonl")
    parser.add_argument("--online", default="nlu_pipeline/data/raw/online/nlu_decisions.jsonl")
    parser.add_argument("--interactions", default="nlu_pipeline/data/raw/online/dashboard_interactions.jsonl")
    parser.add_argument(
        "--interactions-backfill",
        default="nlu_pipeline/data/raw/online/dashboard_interactions_backfill.jsonl",
    )
    parser.add_argument("--online-batch", default="nlu_pipeline/data/raw/online_batch/commands_online_batch.jsonl")
    parser.add_argument("--phase43", default="", help=argparse.SUPPRESS)
    parser.add_argument("--out", default="nlu_pipeline/data/interim/unlabeled_pool.jsonl")
    args = parser.parse_args()

    schema = load_yaml(Path("nlu_pipeline/configs/label_schema.yaml"))
    allowed_intents = set(str(x) for x in schema.get("intents", []))

    online_batch_path = str(args.online_batch or "").strip() or str(args.phase43 or "").strip()
    rows: List[Dict[str, Any]] = []

    for p in [args.logs, args.web, args.hf_dialogue, args.synth]:
        for row in read_jsonl(Path(p)):
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            rows.append(make_base_row(row, default_source="unknown"))

    if online_batch_path:
        for row in read_jsonl(Path(online_batch_path)):
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            rows.append(make_base_row(row, default_source="online_batch"))

    for row in read_jsonl(Path(args.online)):
        text = str(row.get("command", "")).strip()
        if not text:
            continue
        nlu_source = str(row.get("source", "")).strip()
        intent = row.get("intent")
        try:
            conf = float(row.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        labeled_intent = None
        if nlu_source == "nlu_route" and isinstance(intent, str) and intent in allowed_intents and conf >= 0.90:
            labeled_intent = intent

        rows.append(
            make_base_row(
                {
                    "id": row.get("id"),
                    "text": text,
                    "source": "online_decision",
                    "intent": labeled_intent,
                    "slots": {},
                    "risk_level": row.get("risk_level"),
                    "label_source": "runtime_decision_route_high_conf" if labeled_intent else None,
                    "confidence": conf if labeled_intent else None,
                    "nlu_source": nlu_source,
                    "nlu_reason": row.get("reason"),
                },
                default_source="online_decision",
            )
        )

    for p in [args.interactions, args.interactions_backfill]:
        for row in read_jsonl(Path(p)):
            text = str(row.get("utterance") or row.get("text") or "").strip()
            if not text:
                continue
            nlu = row.get("nlu") if isinstance(row.get("nlu"), dict) else {}
            nlu_source = str(nlu.get("source", "")).strip()
            nlu_intent = str(nlu.get("intent", "")).strip()
            try:
                nlu_conf = float(nlu.get("confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                nlu_conf = 0.0

            labeled_intent = None
            label_source = None
            confidence = None
            if nlu_source == "nlu_route" and nlu_intent in allowed_intents and nlu_conf >= 0.90:
                labeled_intent = nlu_intent
                label_source = "dashboard_nlu_route_high_conf"
                confidence = nlu_conf
            elif str(row.get("channel", "")) == "enemy_chat":
                labeled_intent = "fallback_other"
                label_source = "dashboard_enemy_chat"
                confidence = 0.90

            rows.append(
                make_base_row(
                    {
                        "id": row.get("id"),
                        "text": text,
                        "source": "online_interaction",
                        "intent": labeled_intent,
                        "slots": {},
                        "risk_level": "low",
                        "label_source": label_source,
                        "confidence": confidence,
                        "event_type": row.get("event_type"),
                        "channel": row.get("channel"),
                        "response_message": row.get("response_message"),
                        "nlu": nlu,
                    },
                    default_source="online_interaction",
                )
            )

    dedup: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        key = norm_text(text)
        if not key:
            continue

        prev = dedup.get(key)
        if prev is None or row_score(row) > row_score(prev):
            dedup[key] = row

    out_rows = list(dedup.values())
    write_jsonl(Path(args.out), out_rows)
    print(f"[build_unlabeled_pool] in={len(rows)} dedup={len(out_rows)} out={args.out}")


if __name__ == "__main__":
    main()
