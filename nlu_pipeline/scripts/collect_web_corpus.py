from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, List

from common import read_jsonl, text_id, write_jsonl

VERB_HINTS = [
    "建造",
    "生产",
    "训练",
    "展开",
    "部署",
    "攻击",
    "进攻",
    "侦察",
    "采矿",
    "查询",
    "查看",
    "拉矿",
    "爆兵",
    "推",
    "build",
    "train",
    "attack",
    "scout",
    "harvest",
    "expand",
]


def split_sentences(text: str) -> List[str]:
    chunks = re.split(r"[。！？!?,，;；\n]+", text)
    return [c.strip() for c in chunks if c.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--in",
        dest="in_path",
        default="nlu_pipeline/data/raw/web/sources/web_snippets.jsonl",
    )
    parser.add_argument(
        "--out",
        default="nlu_pipeline/data/raw/web/commands_from_web.jsonl",
    )
    args = parser.parse_args()

    in_path = Path(args.in_path)
    rows = read_jsonl(in_path)

    out_rows: List[Dict] = []
    for row in rows:
        src_text = str(row.get("text", ""))
        for cand in split_sentences(src_text):
            if len(cand) < 2 or len(cand) > 80:
                continue
            low = cand.lower()
            if not any(v in cand or v in low for v in VERB_HINTS):
                continue
            out_rows.append(
                {
                    "id": text_id(cand),
                    "text": cand,
                    "source": "web",
                    "url": row.get("url"),
                    "title": row.get("title"),
                }
            )

    write_jsonl(Path(args.out), out_rows)
    print(f"[collect_web_corpus] in={len(rows)} out={len(out_rows)} path={args.out}")


if __name__ == "__main__":
    main()
