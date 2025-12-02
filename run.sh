#!/usr/bin/env bash

VENV_DIR=".venv"

# 1. venv 不存在 → 自动创建
if [ ! -d "$VENV_DIR" ]; then
  echo "🌱 Creating virtual environment..."
  python3 -m venv $VENV_DIR
fi

# 2. 激活 venv
source $VENV_DIR/bin/activate

# 3. 运行
python main.py