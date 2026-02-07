from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List

from common import text_id, write_jsonl


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="in_path", default="Logs/dashboard_events.jsonl")
    parser.add_argument("--out", default="nlu_pipeline/data/raw/online/dashboard_interactions_backfill.jsonl")
    parser.add_argument("--report", default="nlu_pipeline/reports/dashboard_interactions_backfill_report.json")
    args = parser.parse_args()

    events = read_jsonl(Path(args.in_path))
    pending_commands: Deque[Dict[str, Any]] = deque()
    out_rows: List[Dict[str, Any]] = []

    paired_command = 0

    for rec in events:
        event = str(rec.get("event", ""))
        ts = int(rec.get("ts_ms", 0) or 0)
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        if event == "client_message":
            msg_type = str(payload.get("type", ""))
            body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
            if msg_type == "command":
                text = str(body.get("command", "")).strip()
                if text:
                    pending_commands.append({"text": text, "ts": ts})
            continue

        if event != "server_broadcast":
            continue

        msg_type = str(payload.get("type", ""))
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if msg_type == "result":
            response_message = str(body.get("message", "")).strip()
            nlu = body.get("nlu") if isinstance(body.get("nlu"), dict) else {}
            if pending_commands:
                req = pending_commands.popleft()
                paired_command += 1
                utterance = req["text"]
                start_ts = int(req.get("ts", ts) or ts)
                out_rows.append(
                    {
                        "id": text_id(f"cmd::{utterance}::{response_message}::{start_ts}"),
                        "timestamp": start_ts,
                        "event_type": "dashboard_command",
                        "source": "dashboard_backfill",
                        "actor": "human",
                        "channel": "dashboard_command",
                        "utterance": utterance,
                        "response_message": response_message,
                        "success": bool(body.get("success", False)),
                        "nlu": nlu,
                    }
                )
    # Keep unmatched user utterances to widen online corpus.
    for req in pending_commands:
        utterance = str(req.get("text", "")).strip()
        if not utterance:
            continue
        out_rows.append(
            {
                "id": text_id(f"cmd-unpaired::{utterance}::{req.get('ts', 0)}"),
                "timestamp": int(req.get("ts", 0) or 0),
                "event_type": "dashboard_command_unpaired",
                "source": "dashboard_backfill",
                "actor": "human",
                "channel": "dashboard_command",
                "utterance": utterance,
                "response_message": "",
                "success": None,
                "nlu": {},
            }
        )

    write_jsonl(Path(args.out), out_rows)
    report = {
        "input_events": len(events),
        "output_rows": len(out_rows),
        "paired_command": paired_command,
        "unpaired_command": len(pending_commands),
        "in_path": args.in_path,
        "out_path": args.out,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[collect_dashboard_interactions] events={len(events)} out={len(out_rows)} "
        f"paired_cmd={paired_command}"
    )


if __name__ == "__main__":
    main()
