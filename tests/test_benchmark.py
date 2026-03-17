from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark


def setup_function() -> None:
    benchmark.clear()


def test_timed_records_queryable_result() -> None:
    @benchmark.timed("llm_call")
    def fake_call() -> str:
        time.sleep(0.001)
        return "ok"

    assert fake_call() == "ok"

    results = benchmark.query(tag="llm_call")

    assert len(results) == 1
    assert results[0].tag == "llm_call"
    assert results[0].duration_ms >= 0


def test_query_filters_by_time_and_top_n() -> None:
    with benchmark.span("tool_exec", name="slow"):
        time.sleep(0.003)
    with benchmark.span("tool_exec", name="fast"):
        time.sleep(0.001)

    latest_end = benchmark.query(tag="tool_exec", top_n=1)[0].ended_at
    earlier = latest_end - timedelta(seconds=1)

    results = benchmark.query(tag="tool_exec", start_time=earlier, top_n=1)

    assert len(results) == 1
    assert results[0].name == "slow"


def test_export_json_writes_records() -> None:
    with benchmark.span("world_refresh", name="refresh", metadata={"cycle": 1}):
        time.sleep(0.001)

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = Path(tmpdir) / "benchmark.json"
        payload = benchmark.export_json(output_path, tag="world_refresh")

        written = json.loads(output_path.read_text(encoding="utf-8"))
        serialized = json.loads(payload)

    assert len(written) == 1
    assert written == serialized
    assert written[0]["tag"] == "world_refresh"
