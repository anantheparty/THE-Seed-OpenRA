from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List

from common import PROJECT_ROOT, text_id, write_jsonl

PATTERNS = [
    ("human_command", re.compile(r"SimpleExecutor: processing command: (.+)$")),
    ("enemy_strategy", re.compile(r"Strategy decision: (.+)$")),
    ("enemy_llm_response", re.compile(r"LLM strategy response: (.+)$")),
]


def extract_from_log(path: Path) -> List[Dict]:
    rows: List[Dict] = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            for source_tag, pattern in PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                text = m.group(1).strip()
                if not text:
                    continue
                rows.append(
                    {
                        "id": text_id(text),
                        "text": text,
                        "source": "log",
                        "source_tag": source_tag,
                        "log_file": path.name,
                        "line_no": line_no,
                    }
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-dir", default=str(PROJECT_ROOT / "Logs"))
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "nlu_pipeline/data/raw/logs/commands_from_logs.jsonl"),
    )
    args = parser.parse_args()

    logs_dir = Path(args.logs_dir)
    out = Path(args.out)

    all_rows: List[Dict] = []
    for p in sorted(logs_dir.glob("*.log")):
        all_rows.extend(extract_from_log(p))

    write_jsonl(out, all_rows)
    print(f"[collect_logs] files={len(list(logs_dir.glob('*.log')))} rows={len(all_rows)} out={out}")


if __name__ == "__main__":
    main()
