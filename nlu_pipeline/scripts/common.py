from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def norm_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text


def text_id(text: str) -> str:
    return hashlib.sha1(norm_text(text).encode("utf-8")).hexdigest()[:16]


def stable_shuffle(items: List[Dict[str, Any]], seed: int) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    out = items[:]
    rng.shuffle(out)
    return out


def split_by_ratio(items: List[Dict[str, Any]], train: float, dev: float, seed: int) -> Dict[str, List[Dict[str, Any]]]:
    data = stable_shuffle(items, seed)
    n = len(data)
    n_train = int(n * train)
    n_dev = int(n * dev)
    train_items = data[:n_train]
    dev_items = data[n_train:n_train + n_dev]
    test_items = data[n_train + n_dev:]
    return {"train": train_items, "dev": dev_items, "test": test_items}


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
