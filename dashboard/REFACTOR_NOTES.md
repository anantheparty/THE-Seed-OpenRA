# Dashboard Refactor - v0.3.0

## Date: 2026-01-19

## Summary
重大架构重构：从内联组件定义迁移到模块化组件系统，提升代码质量和可维护性。

## 主要变更

### 1. 架构优化
- **删除** app.rs 中所有内联的组件定义（减少 ~400 行代码）
- **启用** components/ 目录下的模块化组件系统
- **重构** app.rs 从 783 行减少到约 350 行

### 2. 组件系统激活
移除 components/mod.rs 中的 `#[allow(dead_code)]` 标记，正式启用：
- ✅ `LeftPanel` - FSM 状态面板
- ✅ `RightPanel` - 实时指标面板
- ✅ `TabView` - 选项卡系统
- ✅ `MetricsCard` / `MetricsCardCompact` - 指标卡片
- ✅ `LogViewer` - 日志查看器
- ✅ `HistoryTimeline` - FSM 转换历史
- ✅ `MemoryView` - 内存监控
- ✅ `ActionDetailView` - 动作详情（新增）

### 3. UI 布局改进
从两栏布局升级到**三栏布局**：
```
┌─────────────────────────────────────────────────────┐
│  Top Bar (标题、连接状态)                             │
├──────────┬──────────────────────────┬───────────────┤
│  Left    │  Center (TabView)        │  Right        │
│  Panel   │  ├─ Agent Benchmark      │  Panel        │
│  (FSM)   │  ├─ Trace & Logs         │  (Metrics)    │
│          │  ├─ Memory               │               │
│          │  └─ Game State           │               │
├──────────┴──────────────────────────┴───────────────┤
│  Bottom Bar (命令输入)                                │
└─────────────────────────────────────────────────────┘
```

### 4. 功能增强

#### LeftPanel
- ✅ 添加滚动视图显示完整计划列表
- ✅ 计划步骤状态显示（✓ 已完成、▶ 当前、空 待执行）

#### RightPanel (新增)
- ✅ 实时连接状态显示
- ✅ Agent 指标占位区
- ✅ 与 TopBar 连接状态同步

#### Agent Benchmark Tab
现在显示 **7 个完整指标**：
- Tokens / Min
- LLM Calls / Min
- Active Tasks
- Total Actions
- Execution Volume
- Failure Rate
- Recovery Rate

#### Memory Tab
使用 `MemoryStatsCard` 显示：
- Total Entries
- Recent Queries
- New Additions

#### Game Benchmark Tab
使用 `MetricsCard` 显示：
- FPS
- Frame Time
- Tick Rate
- Entity Count

### 5. 代码质量提升
- **消除重复**：SDF shader 代码集中到组件中
- **类型安全**：所有组件都有明确的 live_design 定义
- **可维护性**：每个组件独立文件，职责单一
- **可扩展性**：新增组件只需在 components/ 添加即可

## 文件变更

### 修改的文件
- `src/app.rs` - 主应用（783 → ~350 行）
- `src/components/mod.rs` - 移除 dead_code 标记
- `src/components/left_panel.rs` - 添加 plan_list 滚动视图

### 未修改但被激活的组件
- `src/components/right_panel.rs`
- `src/components/tab_view.rs`
- `src/components/metrics_card.rs`
- `src/components/log_viewer.rs`
- `src/components/history_timeline.rs`
- `src/components/memory_view.rs`

## 破坏性变更

### UI ID 路径更新
由于使用了组件层级，UI 元素的访问路径发生变化：

**之前**:
```rust
self.ui.label(id!(current_state)).set_text(cx, &text);
```

**现在**:
```rust
self.ui.label(id!(left_panel.current_state)).set_text(cx, &text);
```

### 受影响的 ID 路径
- `current_state` → `left_panel.current_state`
- `goal` → `left_panel.goal`
- `step_info` → `left_panel.step_info`
- `plan_list` → `left_panel.plan_list`
- `agent_tab` → `center_panel.agent_tab`
- `tokens_card.value` → `center_panel.agent_content.tokens_card.value`
- 等等...

## 版本号更新
- **v0.2.0** → **v0.3.0**

## 已知限制

1. **日志动态显示**：目前 TraceTab 中的 LogViewer 还是静态占位，需要后续实现动态日志条目添加
2. **FSM 历史时间线**：HistoryTimeline 目前显示占位数据，需要集成 trace_events 数据源
3. **右侧面板指标**：RightPanel 的 metrics_placeholder 需要连接实时数据流

## 下一步计划

1. 实现动态 LogViewer（添加/移除日志条目）
2. 集成 HistoryTimeline 与 trace_events
3. 完善 RightPanel 实时指标显示
4. 添加图表可视化功能
5. 实现数据导出功能（CSV/JSON）

## 测试建议

运行 dashboard 并验证：
1. ✅ 三栏布局正常显示
2. ✅ 左侧 FSM 状态面板显示正常
3. ✅ 右侧实时指标面板显示
4. ✅ 选项卡切换功能正常
5. ✅ 连接状态同步更新（TopBar + RightPanel）
6. ✅ Agent 指标卡片显示 7 个指标
7. ✅ Memory 统计卡片显示 3 个指标
8. ✅ Game 指标卡片显示 4 个指标
9. ✅ Plan 列表支持滚动

## 性能影响

- **编译时间**：无明显影响（代码总量略微减少）
- **运行时性能**：预期略有提升（组件复用减少重复渲染）
- **内存占用**：无明显变化

## 迁移指南

如果有其他代码依赖 dashboard 的 UI 元素 ID：
1. 检查所有 `id!(...)` 调用
2. 根据新的组件层级更新路径
3. 参考 app.rs 中的更新示例

---

**重构完成时间**: 约 30 分钟
**代码审查**: 建议进行全面测试
**优先级**: 高（架构改进，长期收益）
