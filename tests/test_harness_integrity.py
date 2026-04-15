"""Meta-tests that keep the runtime test harness honest."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import pytest
import ast
from pathlib import Path


def _has_main_runner(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        if not isinstance(node.test.left, ast.Name) or node.test.left.id != "__name__":
            continue
        values: list[object | None] = []
        left = node.test.left
        values.append(left.value if isinstance(left, ast.Constant) else None)
        values.extend(
            comparator.value if isinstance(comparator, ast.Constant) else None
            for comparator in node.test.comparators
        )
        if "__main__" in values:
            return True
    return False


def test_runtime_e2e_files_are_explicitly_layered() -> None:
    root = Path(__file__).resolve().parent
    expected_markers = {
        "test_e2e_adjutant.py": "mock_integration",
        "test_e2e_capability_bootstrap.py": "mock_integration",
        "test_e2e_experts.py": "mock_integration",
        "test_e2e_full.py": "mock_integration",
        "test_e2e_t1.py": "mock_integration",
        "test_live_e2e.py": "live",
    }

    for filename, marker in expected_markers.items():
        text = (root / filename).read_text(encoding="utf-8")
        assert f"pytestmark = pytest.mark.{marker}" in text, filename


def test_pytest_ini_declares_runtime_test_layers() -> None:
    root = Path(__file__).resolve().parent.parent
    text = (root / "pytest.ini").read_text(encoding="utf-8")
    for marker in ("startup_smoke", "contract", "runtime_invariants", "mock_integration", "live"):
        assert f"{marker}:" in text, marker


def test_regular_test_files_delegate_main_runner_to_pytest() -> None:
    root = Path(__file__).resolve().parent
    manual_script_files = {"test_live_e2e.py", "test_llm_benchmark.py"}
    expected = 'raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))'
    for path in sorted(root.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if not _has_main_runner(path):
            continue
        if path.name in manual_script_files:
            assert expected not in text, path.name
            continue
        assert expected in text, path.name


def test_pytest_only_test_files_are_explicit_and_bounded() -> None:
    root = Path(__file__).resolve().parent
    expected_pytest_only = {
        "test_dashboard_publish_contract.py",
        "test_session_browser.py",
        "test_session_history_contract.py",
        "test_task_replay_contract.py",
        "test_task_triage_contract.py",
    }
    actual_pytest_only = {
        path.name
        for path in sorted(root.glob("test_*.py"))
        if not _has_main_runner(path)
    }
    assert actual_pytest_only == expected_pytest_only

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, *sys.argv[1:]]))
