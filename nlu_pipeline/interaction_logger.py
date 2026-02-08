from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_PATH = PROJECT_ROOT / "nlu_pipeline/data/raw/online/dashboard_interactions.jsonl"

_LOCK = threading.Lock()


def _resolve_log_path() -> Path:
    raw = os.getenv("NLU_INTERACTION_LOG_PATH", "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p
    return DEFAULT_LOG_PATH


def append_interaction_event(
    event_type: str,
    payload: Dict[str, Any],
    *,
    timestamp_ms: Optional[int] = None,
) -> None:
    record = {
        "timestamp": int(timestamp_ms or time.time() * 1000),
        "event_type": str(event_type or "").strip() or "unknown",
        **payload,
    }
    path = _resolve_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # logging should never block runtime
        pass

