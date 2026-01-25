# Web Console 实现 Roadmap

## Phase 0: 环境准备 ✅
- [x] 0.1 安装 .NET 6 SDK (6.0.428)
- [x] 0.2 编译 OpenRA (OpenCodeAlert) - `make` 成功
- [x] 0.3 测试 OpenRA 启动 - `./start.sh` 正常
- [x] 0.4 确认 VNC 环境正常 - Xvfb + x11vnc + noVNC

## Phase 1: 基础设施 🏗️
- [x] 1.1 创建 nginx 配置文件
- [x] 1.2 申请 SSL 证书 (certbot) - 有效期至 2026-04-25
- [x] 1.3 配置 Basic Auth (使用现有 .htpasswd)
- [ ] 1.4 测试反代连通性

## Phase 2: 前端框架 🎨
- [ ] 2.1 创建基础 HTML 结构
- [ ] 2.2 实现三区布局 (顶部/中间/底部)
- [ ] 2.3 VNC iframe 集成
- [ ] 2.4 对话区 Tab 切换
- [ ] 2.5 Debug 区折叠功能

## Phase 3: 服务控制 ⚙️
- [ ] 3.1 后端 API (Python Flask/FastAPI)
- [ ] 3.2 Git Pull 功能
- [ ] 3.3 编译功能 (dotnet build)
- [ ] 3.4 启动/停止/重启游戏
- [ ] 3.5 状态轮询

## Phase 4: 对话功能 💬
- [ ] 4.1 WebSocket 连接 Dashboard Bridge
- [ ] 4.2 发送指令
- [ ] 4.3 显示回复
- [ ] 4.4 历史记录
- [ ] 4.5 敌方对话 UI (预留)

## Phase 5: Debug 工具 🔍
- [ ] 5.1 Logs Tab
- [ ] 5.2 Status Tab
- [ ] 5.3 xterm.js 终端
- [ ] 5.4 Tab 扩展机制

## Phase 6: 部署上线 🚀
- [ ] 6.1 启动脚本整合
- [ ] 6.2 服务自动重启 (systemd)
- [ ] 6.3 最终测试
- [ ] 6.4 文档更新

---

## 当前进度

**Phase 1: 基础设施** ← 进行中
