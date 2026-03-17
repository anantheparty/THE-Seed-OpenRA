# xi Phase 5+6 集中审计（Phase 5 Vue 3 看板，`f9505a1`）

审计目标：审 xi 的 Phase 5 Vue 3 看板实现，重点检查：
- WS 连接 + 自动重连
- ChatView 对话主界面（时效性标注）
- TaskPanel 侧栏（Task 列表 + pending question）
- OpsPanel（VNC + 服务控制）
- DiagPanel（日志流 + benchmark）
- 双模式切换（用户/调试）
- 入站/出站消息格式是否匹配 `ws_server`

结论：**不是 zero-gap。**  
构建本身通过，但我认为还有 **3 个 blocker + 3 个 should-fix**。

---

## Findings

### 1. Blocker — `TaskPanel` 的 pending-question 路径实际上是断的，当前实现无法展示也无法正确回复待回答问题

涉及：
- `web-console-v2/src/components/TaskPanel.vue:16-21`
- `web-console-v2/src/components/TaskPanel.vue:37-53`
- `ws_server/server.py:3-5`
- `ws_server/server.py:145-156`

问题有两层：

第一层，前端从未填充 `pendingQuestions`：
- `TaskPanel` 里只有 `task_list` 和 `task_update` 两个 handler
- `pendingQuestions` 被初始化后没有任何更新路径
- 所以 “待回答” 区块在 live runtime 里会一直是空的

第二层，就算手工塞进去了，回复语义也不对：
- `reply(question, answer)` 只是 `props.send('command_submit', { text: answer })`
- 没有带 `message_id`
- 没有带 `task_id`
- 也没有单独的 reply / player-response 入站协议

而 `ws_server` 当前明确支持的入站只有：
- `command_submit`
- `command_cancel`
- `mode_switch`

也就是说，Phase 5 claim 里的 “TaskPanel + pending question” 目前不是“细节未 polish”，而是**整条交互链没有接通**。

### 2. Blocker — `OpsPanel` 里的 VNC 和“服务控制”都没有真正实现，模式切换也没有发到后端

涉及：
- `web-console-v2/src/components/OpsPanel.vue:4-9`
- `web-console-v2/src/components/OpsPanel.vue:25`
- `web-console-v2/src/App.vue:24-26`
- `web-console-v2/src/App.vue:43-49`
- `ws_server/server.py:153-156`

当前 `OpsPanel` 的问题也有三层：

- `VNC iframe` 的 `src` 被写死成 `about:blank`
- 面板里没有任何 service control 行为，只有两个“模式切换”按钮
- 这两个按钮只是 `$emit('mode-switch', ...)` 到父组件，`App.vue` 里只是改本地 `mode.value`

但后端 `ws_server` 是有明确 `mode_switch` 入站协议的。  
当前前端从来没有调用：
- `send('mode_switch', { mode })`

所以“用户/调试模式”只是在浏览器本地切布局，不会同步给后台。按 Wang 给的审计重点，这条不能算完成。

### 3. Blocker — `DiagPanel` 的 benchmark 面板依赖了一个当前 `ws_server` 没有定义的出站契约

涉及：
- `web-console-v2/src/components/DiagPanel.vue:47-65`
- `ws_server/server.py:4-5`
- `ws_server/server.py:182-198`

`DiagPanel` 现在只在一种情况下更新 benchmark：
- 收到 `world_snapshot`
- 且 `msg.data.benchmark` 是一个 record 数组

但当前 `ws_server` 的公开出站 helper 只有：
- `send_world_snapshot(snapshot)`
- `send_task_update(task_data)`
- `send_task_list(tasks)`
- `send_log_entry(entry)`
- `send_player_notification(notification)`
- `send_query_response(response)`

这里没有任何一个 helper 定义了：
- benchmark 专用出站消息
- 或 `world_snapshot` 必须带 `benchmark` records

所以当前 benchmark pane 其实是依赖一个**未声明、未封装、未验证**的隐式 payload 约定。按 “消息格式是否匹配 `ws_server`” 这个审计目标，这里还不能算过。

### 4. Should-fix — 时效性标注会冻结，`ChatView` / `TaskPanel` 没有真正使用响应式 time-ago tick

涉及：
- `web-console-v2/src/components/ChatView.vue:17-19`
- `web-console-v2/src/components/TaskPanel.vue:28-29`
- `web-console-v2/src/composables/useTimeAgo.js:13-18`

`useTimeAgo.js` 里其实已经写了一个会每秒触发更新的 `useTimeAgo()`。  
但 `ChatView` 和 `TaskPanel` 都只 import 了 `formatTimeAgo()`，没有使用 `tick`。

结果是：
- `{{ formatTimeAgo(msg.timestamp) }}` / `{{ formatTimeAgo(task.timestamp) }}`
- 只会在组件因为别的 reactive 变化时重算

如果界面静止不刷新，`just now -> 10s ago -> 1m ago` 这些标签不会自己走。  
这和 Wang 明确点的“时效性标注”目标不一致。

### 5. Should-fix — `useWebSocket.disconnect()` 会触发意外重连，组件卸载后仍可能拉起新连接

涉及：
- `web-console-v2/src/composables/useWebSocket.js:13-16`
- `web-console-v2/src/composables/useWebSocket.js:43-49`

当前逻辑是：
- `disconnect()` 调 `ws.close()`
- 但 `onclose` 无条件 `setTimeout(connect, 3000)`

所以显式断开和组件卸载时：
- 仍会排一个新的 reconnect timer
- 组件已经销毁，socket 也可能被再次拉起

这属于典型的“缺少 manual-close guard”问题。不是这次最大的 blocker，但真实运行会留下幽灵重连。

### 6. Should-fix — `DiagPanel` 对 `log_entry` 的字段假设与当前结构化日志不一致，颜色和标签会退化

涉及：
- `web-console-v2/src/components/DiagPanel.vue:5-9`
- `web-console-v2/src/components/DiagPanel.vue:62`

当前 `DiagPanel` 假设日志 entry 长这样：
- `level` 可能是 `error` / `warning`
- `tag` 是要显示的标签

但 Phase 6 结构化日志的真实字段更接近：
- `level`: `DEBUG / INFO / WARN / ERROR`
- `component`
- `event`
- `message`

因此 live UI 里至少会有两个退化：
- `.log-entry.error` / `.log-entry.warning` 样式不匹配大写 `ERROR` / `WARN`
- `[{{ entry.tag || 'log' }}]` 基本只会显示成默认的 `log`

这不一定会完全阻塞使用，但它说明前端 debug pane 还没有和真实日志 schema 对齐。

---

## What Passed

- `web-console-v2/` 能成功构建
- `useWebSocket` 至少具备基本的自动重连骨架
- `ChatView` / `TaskPanel` / `DiagPanel` 的静态布局方向基本对
- `App.vue` 的用户 / 调试双栏切换在前端本地可工作

---

## 本地验证

我跑了：
- `npm run build`（在 `web-console-v2/`）

另外做了源码对照审计：
- `web-console-v2/src/App.vue`
- `web-console-v2/src/composables/useWebSocket.js`
- `web-console-v2/src/components/ChatView.vue`
- `web-console-v2/src/components/TaskPanel.vue`
- `web-console-v2/src/components/OpsPanel.vue`
- `web-console-v2/src/components/DiagPanel.vue`
- `ws_server/server.py`

---

## 结论

这轮不能清成 `zero-gap`。

我当前的审计口径是：
- **3 个 blocker**
  - pending-question 交互链未接通
  - OpsPanel 的 VNC / service control / 后端模式同步未完成
  - benchmark pane 依赖未声明的 WS 出站契约
- **3 个 should-fix**
  - 时效性标签不会自动更新
  - 显式 disconnect 仍会自动重连
  - `DiagPanel` 日志字段假设和真实结构化日志 schema 不一致
