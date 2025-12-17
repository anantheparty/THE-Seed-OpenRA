#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  cat <<'EOF'
❌ 未检测到 uv，请先安装：
  curl -LsSf https://astral.sh/uv/install.sh | sh
或参考 https://docs.astral.sh/uv/getting-started/ 选择其他方式安装。
EOF
  exit 1
fi

echo "📦 Installing the-seed..."
uv pip install -e ./the-seed

echo "🚀 Launching main.py"
uv run python main.py "$@"