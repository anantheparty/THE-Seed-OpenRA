@echo off
cd /d "%~dp0"

echo Checking for uv...
where uv >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo uv is not installed. Please install it first.
    echo powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    exit /b 1
)

if not exist .venv (
    echo Creating virtual environment...
    uv venv
)

echo Installing dependencies...
uv pip install -e .\the-seed
uv pip install websockets

echo Launching main.py...
uv run python main.py %*
