@echo off
setlocal

cd /d %~dp0

where uv >nul 2>nul
if errorlevel 1 (
    echo ❌ 未检测到 uv，请先安装：
    echo     powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo 或访问 https://docs.astral.sh/uv/getting-started/ 了解更多安装方式。
    exit /b 1
)

echo 📦 Installing the-seed via uv...
uv pip install -e .\the-seed

echo 🚀 Launching main.py with uv...
uv run python main.py %*