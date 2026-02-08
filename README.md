# THE-Seed OpenRA (Web Console Edition)

本项目当前使用 **Web Console + Python 后端**，Rust Dashboard 已移除。

## 架构
```
Web Console (web-console/)
    ↕ WebSocket
main.py (Console Bridge / DashboardBridge, ws://127.0.0.1:8090)
    ↕
OpenRA API (openra_api/)
```

核心执行链路：
`玩家指令 -> 观测状态 -> LLM 生成 Python -> 执行 -> 回传结果`

## 快速启动
### 1) 启动后端
Linux/macOS:
```bash
./run.sh
```

Windows:
```powershell
.\run.bat
```

或直接：
```bash
python3 main.py
```

### 2) 启动 Web Console（可选）
```bash
cd web-console
./start-all.sh
```

默认端口：
- Web Console: `http://127.0.0.1:8000`
- Console Bridge WebSocket: `ws://127.0.0.1:8090`

## 主要目录
```text
.
├── main.py
├── openra_api/           # 项目唯一 OpenRA 客户端 API
├── agents/
│   ├── enemy_agent.py
│   ├── nlu_gateway.py
│   ├── strategy/         # 战略栈（实验接线，可在 WebUI Debug 页签控制）
│   ├── combat/
│   └── economy/
├── openra_state/         # 情报分析模块（复用 openra_api）
├── tactical_core/
├── nlu_pipeline/
└── web-console/
```

## 配置
模型配置通过 `the-seed` 的 `load_config()` 加载，默认模板定义在：
- `the-seed/the_seed/config/schema.py`

建议使用本地配置或环境隔离，不要把私密 key 提交到仓库。
