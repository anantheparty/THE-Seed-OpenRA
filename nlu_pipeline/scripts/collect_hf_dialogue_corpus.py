from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List

from common import load_yaml, norm_text, text_id, write_jsonl

HF_SPLITS_API = "https://datasets-server.huggingface.co/splits"
HF_ROWS_API = "https://datasets-server.huggingface.co/rows"


def http_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_split(source: Dict[str, Any]) -> tuple[str, str]:
    ds = str(source.get("dataset", "")).strip()
    config = str(source.get("config", "")).strip()
    split = str(source.get("split", "")).strip()
    if ds and config and split:
        return config, split

    url = f"{HF_SPLITS_API}?{urllib.parse.urlencode({'dataset': ds})}"
    data = http_json(url)
    splits = data.get("splits", [])
    if not isinstance(splits, list) or not splits:
        raise RuntimeError(f"no split metadata for dataset={ds}")
    first = splits[0] if isinstance(splits[0], dict) else {}
    return str(first.get("config", "default")), str(first.get("split", "train"))


def get_field_path(row: Dict[str, Any], path: str) -> Any:
    cur: Any = row
    for key in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def flatten_texts(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from flatten_texts(item)
        return
    if isinstance(value, dict):
        # Prefer common text-like keys first, then all values.
        preferred = ("text", "content", "input", "instruction", "question", "prompt", "query", "user")
        used = set()
        for k in preferred:
            if k in value:
                used.add(k)
                yield from flatten_texts(value.get(k))
        for k, v in value.items():
            if k in used:
                continue
            yield from flatten_texts(v)


def zh_ratio(text: str) -> float:
    if not text:
        return 0.0
    zh_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    return zh_count / max(len(text), 1)


def clean_text(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" \"'`")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="nlu_pipeline/configs/hf_corpus_sources.yaml")
    parser.add_argument("--out", default="nlu_pipeline/data/raw/web/commands_from_hf_dialogue.jsonl")
    parser.add_argument("--report", default="nlu_pipeline/reports/hf_corpus_collection_report.json")
    parser.add_argument("--report-md", default="nlu_pipeline/reports/hf_corpus_collection_report.md")
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()

    cfg = load_yaml(Path(args.config))
    filters = cfg.get("filters", {})
    min_len = int(filters.get("min_len", 2))
    max_len = int(filters.get("max_len", 80))
    min_zh = float(filters.get("min_zh_ratio", 0.25))
    dedup_enabled = bool(filters.get("dedup", True))
    label_as_fallback = bool((cfg.get("output") or {}).get("label_as_fallback", True))

    sources = cfg.get("sources", [])
    if not isinstance(sources, list):
        raise SystemExit("invalid config: sources must be a list")

    rows_out: List[Dict[str, Any]] = []
    seen = set()
    source_stats: Dict[str, Dict[str, Any]] = {}

    for source in sources:
        if not isinstance(source, dict):
            continue
        ds = str(source.get("dataset", "")).strip()
        if not ds:
            continue
        fields = [str(x).strip() for x in source.get("fields", []) if str(x).strip()]
        if not fields:
            fields = ["text"]
        max_rows = max(0, int(source.get("max_rows", 0)))
        if max_rows <= 0:
            continue

        try:
            config_name, split_name = resolve_split(source)
        except Exception as e:
            source_stats[ds] = {"error": str(e), "accepted": 0, "fetched_rows": 0}
            continue

        accepted = 0
        fetched_rows = 0
        offset = 0
        page_size = max(1, min(int(args.page_size), 200))

        while accepted < max_rows:
            params = {
                "dataset": ds,
                "config": config_name,
                "split": split_name,
                "offset": offset,
                "length": page_size,
            }
            url = f"{HF_ROWS_API}?{urllib.parse.urlencode(params)}"
            try:
                data = http_json(url)
            except Exception:
                break

            page = data.get("rows", [])
            if not isinstance(page, list) or not page:
                break

            fetched_rows += len(page)
            for wrapped in page:
                row_obj = wrapped.get("row") if isinstance(wrapped, dict) else {}
                if not isinstance(row_obj, dict):
                    continue
                row_idx = wrapped.get("row_idx") if isinstance(wrapped, dict) else None

                for field in fields:
                    value = get_field_path(row_obj, field)
                    for raw_text in flatten_texts(value):
                        text = clean_text(raw_text)
                        if len(text) < min_len or len(text) > max_len:
                            continue
                        if zh_ratio(text) < min_zh:
                            continue

                        key = norm_text(text)
                        if dedup_enabled and key in seen:
                            continue
                        seen.add(key)

                        rec: Dict[str, Any] = {
                            "id": text_id(f"{ds}|{field}|{text}"),
                            "text": text,
                            "source": "web_hf",
                            "source_tag": "hf_dialogue",
                            "dataset": ds,
                            "config": config_name,
                            "split": split_name,
                            "field": field,
                            "row_idx": row_idx,
                        }
                        if label_as_fallback:
                            rec.update(
                                {
                                    "intent": "fallback_other",
                                    "slots": {},
                                    "risk_level": "low",
                                    "label_source": "hf_open_corpus",
                                    "confidence": 0.92,
                                }
                            )
                        rows_out.append(rec)
                        accepted += 1
                        if accepted >= max_rows:
                            break
                    if accepted >= max_rows:
                        break
                if accepted >= max_rows:
                    break

            offset += len(page)
            if len(page) < page_size:
                break

        source_stats[ds] = {
            "accepted": accepted,
            "fetched_rows": fetched_rows,
            "config": config_name,
            "split": split_name,
            "fields": fields,
            "max_rows": max_rows,
        }

    write_jsonl(Path(args.out), rows_out)

    report = {
        "output_rows": len(rows_out),
        "output_path": args.out,
        "config_path": args.config,
        "sources": source_stats,
        "filters": {
            "min_len": min_len,
            "max_len": max_len,
            "min_zh_ratio": min_zh,
            "dedup": dedup_enabled,
        },
        "label_as_fallback": label_as_fallback,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# HF Dialogue Corpus Collection Report",
        "",
        f"- output_rows: `{len(rows_out)}`",
        f"- output_path: `{args.out}`",
        f"- config_path: `{args.config}`",
        "",
        "## Source Stats",
    ]
    for ds, stat in sorted(source_stats.items()):
        if "error" in stat:
            md_lines.append(f"- {ds}: ERROR `{stat['error']}`")
            continue
        md_lines.append(
            f"- {ds}: accepted={stat['accepted']} fetched_rows={stat['fetched_rows']} "
            f"split={stat['config']}/{stat['split']}"
        )
    Path(args.report_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"[collect_hf_dialogue_corpus] out={len(rows_out)} path={args.out}")


if __name__ == "__main__":
    main()

