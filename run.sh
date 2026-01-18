#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# Cleanup function to kill background processes
cleanup() {
  echo ""
  echo "🛑 Shutting down..."
  if [ -n "${BACKEND_PID:-}" ]; then
    kill $BACKEND_PID 2>/dev/null || true
    echo "  ✓ Backend stopped"
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

# Source cargo environment if available
if [ -f "$HOME/.cargo/env" ]; then
  source "$HOME/.cargo/env"
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

echo "🚀 Starting Python backend (background)..."
uv run python main.py "$@" > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
echo "  ✓ Backend PID: $BACKEND_PID"
echo "  📊 Backend logs: /tmp/backend.log"

# Wait for backend to start
sleep 2

echo "🎨 Launching Dashboard (foreground - window will open)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
cd "$ROOT_DIR/dashboard"
cargo run --release