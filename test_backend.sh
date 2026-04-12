#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Runtime startup smoke"
python3 -m pytest -m startup_smoke tests/test_game_control.py -q

echo
echo "==> Runtime wiring contracts"
python3 -m pytest -m contract tests/test_game_control.py tests/test_ws_and_review.py -q

echo
echo "==> Runtime degradation invariants"
python3 -m pytest -m runtime_invariants tests/test_world_model.py tests/test_game_loop.py -q

echo
echo "==> Capability bootstrap mock-integration"
python3 -m pytest -m mock_integration tests/test_e2e_capability_bootstrap.py -q

echo
echo "==> Unit-request gating contracts"
python3 -m pytest tests/test_unit_request.py -q -k \
  "same_task_multiple_blocking_requests_remain_task_gated_until_all_ready"

echo
echo "Layered backend gate passed."
echo "This verifies the current runtime surface can:"
echo "  - start ApplicationRuntime with WS enabled"
echo "  - keep task/WS wiring contracts aligned"
echo "  - answer a real sync_request over WebSocket"
echo "  - degrade predictably on stale/disconnect conditions"
echo "  - fulfill the request_units -> capability bootstrap path"
echo "  - preserve current task-level gating semantics for multiple blocking requests on one task"
echo "  - publish background dashboard/task updates without async task crashes"
echo "  - stop cleanly and release the WS port"
echo
echo "It does not claim full live-game correctness."
echo "For live game-in-loop checks, run:"
echo "  python3 tests/test_live_e2e.py phase_a"
