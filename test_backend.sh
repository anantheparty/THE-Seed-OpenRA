#!/usr/bin/env bash
# High-signal backend self-check for the current runtime shape.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "[1/2] Running startup smoke..."
python3 -m pytest tests/test_game_control.py -q -m startup_smoke

echo ""
echo "[2/2] Running bridge / websocket contracts..."
python3 -m pytest tests/test_game_control.py tests/test_ws_and_review.py -q -m contract

echo ""
echo "Backend startup contracts are green."
echo "This checks the current runtime path only; live game-in-loop E2E is still a separate manual gate."
