# THE-Seed OpenRA Agent & Dashboard

本项目是一个基于 `the-seed` 框架构建的 OpenRA智能体，并配备了基于 Makepad 的高性能 Rust 可视化 Dashboard。

智能体通过 `the-seed` 框架的 FSM (有限状态机) 理解游戏状态并执行决策，Dashboard 则通过 WebSocket 实时展示智能体的思考过程、黑板数据和游戏状态。

## 📋 目录
- [环境要求](#环境要求)
- [快速启动](#快速启动)
  - [1. 启动智能体 (Python 后端)](#1-启动智能体-python-后端)
  - [2. 启动可视化 Dashboard (Rust 前端)](#2-启动可视化-dashboard-rust-前端)
- [项目结构](#项目结构)

## 环境要求

### 基础环境
*   **OpenRA**: 需要安装并运行 OpenRA (推荐使用配合本项目的版本)，并确保开启外部 API 支持（默认端口 `7445`）。

### Python 环境 (智能体)
*   **uv**: 极速 Python 包管理器。
    *   安装方式 (Windows): `powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"`
    *   安装方式 (Linux/Mac): `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Rust 环境 (Dashboard)
*   **Rust**: 需要安装 Rust 编程语言。
*   **Nightly Toolchain**: Makepad 依赖 Rust Nightly 版本。
*   **Wasm/Android 构建工具 (可选)**: 如果需要编译为 Web 或移动端版本，需安装 `cargo-makepad`。

## 快速启动

建议按以下顺序启动，以获得最佳体验。

### 1. 启动智能体 (Python 后端)
我们提供了跨平台的一键启动脚本，会自动使用 `uv` 创建虚拟环境、安装 `the-seed` 依赖并启动主程序。

**Windows:**
```powershell
.\run.bat
```

**Linux / macOS:**
```bash
./run.sh
```

启动成功后，控制台将显示日志，并且 WebSocket Server 会在 `ws://0.0.0.0:8080` 监听 Dashboard 连接。

### 2. 启动可视化 Dashboard (Rust 前端)
保持 Python 智能体运行，打开一个新的终端窗口，进入 `dashboard` 目录并运行：

```powershell
cd dashboard
cargo run
```

*   **首次运行**: Cargo 会下载依赖并编译 Makepad 及其资源，可能需要几分钟。
*   **运行界面**: 启动后将弹出一个独立窗口。
    *   **左上角状态**: 显示 "Connected" (绿色) 表示已连接到智能体。
    *   **FSM STATE**: 实时显示智能体当前的思维状态 (如 OBSERVE, PLAN, ACTION_GEN)。
    *   **BLACKBOARD**: 实时滚动显示智能体的记忆黑板、当前计划步骤和执行结果。
    *   **User Command**: 底部输入框允许你直接向智能体发送指令（需在智能体逻辑中自行处理 `NEED_USER` 状态）。（好像还用不了）

## 项目结构

```text
.
├── main.py                 # 智能体入口 (Python)
├── agents/                 # 智能体逻辑实现
├── dashboard/              # 可视化前端 (Rust + Makepad)
│   ├── src/
│   │   ├── main.rs         # Rust 入口
│   │   ├── app.rs          # UI 布局与逻辑
│   │   └── ws_client.rs    # WebSocket 客户端实现
│   └── Cargo.toml
├── the-seed/               # 核心框架子模块
├── run.bat                 # Windows 启动脚本
└── run.sh                  # Linux/Mac 启动脚本
```
