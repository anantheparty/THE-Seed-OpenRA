# xi Phase 5 最终回归审计（`9a0158e`）

## 结论

这轮我给出 **`zero blockers`**。

`9a0158e` 已经把上一轮遗留的 2 个 blocker 关掉：

1. `App.vue` 顶部 mode toggle 现在会发送 `mode_switch`，前后端模式失同步的主路径已关闭。
2. `ws_server.send_task_list()` 现在支持携带 `pending_questions`，`TaskPanel` 依赖的正式 payload 契约已补齐。

我还确认了 `DiagPanel` 的日志级别样式兼容修复已经到位，以及 `ChatView` 的 time-ago 现在会按 1s 刷新。

不过还留有 **1 个非阻塞 UI nit**：`TaskPanel` 本身仍直接调用静态 `formatTimeAgo()`，没有接入 reactive ticker，所以左侧 task 列表里的 time-ago 文案仍会冻结；这不影响 Phase 5 的核心功能闭环。

## 已关闭项

### 1. 已关闭 — 顶部 mode toggle 现在会发 `mode_switch`

文件：`web-console-v2/src/App.vue`

当前实现：

```vue
function toggleMode() {
  mode.value = mode.value === 'user' ? 'debug' : 'user'
  send('mode_switch', { mode: mode.value })
}
```

这关闭了我上一轮指出的 header toggle 只改本地状态、不通知后端的问题。

### 2. 已关闭 — `task_list` helper 现在支持 `pending_questions`

文件：`ws_server/server.py`

当前实现：

```python
async def send_task_list(
    self,
    tasks: list[dict[str, Any]],
    pending_questions: Optional[list[dict[str, Any]]] = None,
) -> None:
    payload: dict[str, Any] = {"tasks": tasks}
    if pending_questions is not None:
        payload["pending_questions"] = pending_questions
    await self.broadcast("task_list", payload)
```

这关闭了上一轮 `TaskPanel` 已经读 `msg.data.pending_questions`，但正式 WS helper 仍发不出去该字段的契约缺口。

### 3. 已关闭 — `DiagPanel` 颜色映射现在兼容大写结构化日志级别

文件：`web-console-v2/src/components/DiagPanel.vue`

当前实现同时修了两层：

- class 绑定改为 `entry.level?.toLowerCase()`
- CSS 同时兼容 `.warn` / `.warning`

因此结构化日志的 `ERROR` / `WARN` 现在能正确落到样式类。

### 4. 部分关闭 — time-ago 自动刷新只覆盖了 `ChatView`

文件：`web-console-v2/src/components/ChatView.vue`

这轮确实新增了 1s interval 驱动的 reactive refresh，因此聊天区 time-ago 已不再冻结。

## 剩余非阻塞项

### 1. Nit — `TaskPanel` 的 time-ago 仍不会自动刷新

文件：`web-console-v2/src/components/TaskPanel.vue`

当前模板仍是：

```vue
优先级: {{ task.priority }} · {{ formatTimeAgo(task.timestamp) }}
```

而组件内部没有像 `ChatView` 那样引入任何 reactive tick / `useTimeAgo()`。

因此：

- 初次渲染能显示正确文案
- 但若没有新的响应式更新触发重渲染，时间文本会停住

这属于 UI polish 问题，不影响：

- pending question 显示/回复
- mode switch 同步
- benchmark / log stream 展示
- 断开连接行为

## 本地验证

我执行了：

- `npm run build`（`web-console-v2/`）
- `python3 tests/test_ws_and_review.py`
- 直接对照源码与 WS 契约：`App.vue`、`TaskPanel.vue`、`ChatView.vue`、`DiagPanel.vue`、`ws_server/server.py`

结果：

- `vite build` 通过
- `WS + review_interval`：`7 / 7` 通过
- 上轮 2 个 blocker 均确认关闭

## 最终判断

按我的审计口径，这轮是 **`zero blockers`**，`Phase 5 + 6` 可以关闭。

我只建议把 `TaskPanel` 的 time-ago reactive 化作为后续顺手清理项，不需要阻塞进入 `Phase 7`。
