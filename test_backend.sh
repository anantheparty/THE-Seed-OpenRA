#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Runtime startup smoke"
python3 -m pytest -m startup_smoke tests/test_game_control.py -q

echo
echo "Smoke check passed."
echo "This verifies the current runtime assembly can:"
echo "  - start ApplicationRuntime with WS enabled"
echo "  - answer a real sync_request over WebSocket"
echo "  - publish background dashboard/task updates without async task crashes"
echo "  - stop cleanly and release the WS port"
echo
echo "For live game-in-loop checks, run:"
echo "  python3 tests/test_live_e2e.py phase_a"
