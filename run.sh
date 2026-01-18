#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Cleanup function to kill background processes
cleanup() {
  echo ""
  echo "🛑 Shutting down..."
  if [ -n "${DASHBOARD_PID:-}" ]; then
    kill $DASHBOARD_PID 2>/dev/null || true
    echo "  ✓ Dashboard stopped"
  fi
  exit 0
}

trap cleanup SIGINT SIGTERM EXIT

if ! command -v uv >/dev/null 2>&1; then
  cat <<'EOF'
❌ 未检测到 uv，请先安装：
  curl -LsSf https://astral.sh/uv/install.sh | sh
或参考 https://docs.astral.sh/uv/getting-started/ 选择其他方式安装。
EOF
  exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
  cat <<'EOF'
❌ 未检测到 cargo，请先安装 Rust：
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
或参考 https://www.rust-lang.org/tools/install
EOF
  exit 1
fi

echo "📦 Installing the-seed..."
uv pip install -e ./the-seed

if [ -f requirements.txt ]; then
  echo "🧰 Installing project requirements..."
  uv pip install -r requirements.txt
fi

echo "🎨 Starting Dashboard (background)..."
cd "$ROOT_DIR/dashboard"
cargo run --release > /tmp/dashboard.log 2>&1 &
DASHBOARD_PID=$!
echo "  ✓ Dashboard PID: $DASHBOARD_PID"
echo "  📊 Dashboard logs: /tmp/dashboard.log"

# Wait for dashboard to start
sleep 2

cd "$ROOT_DIR"
echo "🚀 Launching Python backend (main.py)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
uv run python main.py "$@"