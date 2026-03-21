# OpenCodeAlert GameAPI 刷屏调研与修复

## 结论

OpenCodeAlert 控制台刷屏的根因不在 Python 侧日志，也不在 `CopilotDebug` 控制的 JSON dump，而是在 `CopilotCommandServer` 的连接接受循环：

- `openra_api.GameAPI` 当前是“每次请求开一个短连接”
- `CopilotCommandServer.AcceptAsync()` 每接到一个连接，之前都会打印一条 `INFO: 接受新的客户端连接`
- `WorldModel.refresh()` 高频轮询时，这条 `INFO` 会随每次查询刷屏

所以真正的噪声源是“每请求一连一断”的连接级日志，而不是命令内容本身。

## 代码定位

相关位置：

- `OpenCodeAlert/OpenRA.Game/CopilotCommandServer.cs`
- `OpenCodeAlert/OpenRA.Game/Settings.cs`
- `OpenCodeAlert/OpenRA.Game/World.cs`

已有开关：

- `Settings.Game.CopilotDebug`
- `World` 在创建 `CopilotCommandServer` 后会执行 `CopilotServer.DebugMode = gameSettings.CopilotDebug`

现状判断：

- 接收/发送 JSON 内容打印已经只在 `DebugMode` 下启用
- 但 `AcceptAsync()` 的连接接受日志此前是无条件 `LogInfo(...)`
- 因此即使 `CopilotDebug = false`，高频轮询仍然会刷屏

## 修复

已在 `OpenCodeAlert/OpenRA.Game/CopilotCommandServer.cs` 做最小修复：

- 新增 `LogDebug(...)`
- 将以下高频连接级日志从 `LogInfo(...)` 改为 `LogDebug(...)`
  - `接受新的客户端连接`
  - `服务器正在停止，退出Accept循环`
  - `Socket已被释放，退出Accept循环`

保留不变的日志：

- 服务启动成功
- 服务停止
- `Accept` / handler 异常
- 其他错误日志

这样：

- 默认运行时不会再被每次 GameAPI 请求刷屏
- 当 `CopilotDebug = true` 时，仍能看到连接级日志和 JSON 请求/响应内容，保留排障能力

## 验证

已执行：

```bash
dotnet build OpenCodeAlert/OpenRA.Game/OpenRA.Game.csproj
```

结果：

- `0 Error`
- 仅有项目内既有 analyzer / style warnings

## 后端侧建议

### 1. 是否还要降低 `WorldModel` 刷新频率

建议作为独立优化项考虑，但不是这次刷屏问题的主修复。

当前默认刷新策略是：

- actor：`0.1s`
- economy：`0.5s`
- map：`1.0s`

由于 `GameAPI` 仍是同步 socket 且每请求一连一断，这个 actor 刷新频率在 live 模式下偏激进。即使日志不再刷屏，它仍然会带来较高请求量。

建议：

- live 默认可考虑把 actor 刷新从 `0.1s` 放宽到 `0.2s` 或 `0.25s`
- benchmark / debug 模式保留更高频率
- 最好做成 refresh profile，而不是写死单一值

### 2. 能否“只在数据变化时刷新”

当前架构下基本不能真正做到。

原因：

- Python 侧只有 pull API，没有 push/event stream
- 在不先发请求的情况下，客户端并不知道游戏内状态是否变化

能做的只有：

- 分层轮询
- 自适应降频
- 对低价值查询做缓存/采样

如果要实现真正的“只在变化时刷新”，需要游戏侧增加主动事件推送或长连接订阅机制。

## 建议结论

短期建议直接采用本次修复：

- 把连接级请求日志降到 `CopilotDebug` 下

中期建议：

- 给 `WorldModel` 增加 live/debug 两档 refresh profile
- 后续如果还要继续降噪和降负载，再评估游戏侧 push/event API
