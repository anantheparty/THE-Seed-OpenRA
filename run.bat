@echo off

set VENV=.venv

if not exist %VENV% (
    echo 🌱 Creating virtual environment...
    python -m venv %VENV%
)

call %VENV%\Scripts\activate

pip show the-seed >nul 2>nul
if errorlevel 1 (
    echo 📦 Installing the-seed into venv...
    pip install -e ./the-seed
)

pip install -r requirements.txt

python main.py