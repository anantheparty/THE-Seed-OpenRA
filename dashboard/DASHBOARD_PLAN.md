# THE SEED - OpenRA Dashboard 开发计划

## 一、现状分析

### 1.1 已实现功能 ✓
- WebSocket 客户端连接（ws_client.rs）
- 基础 UI 框架（makepad-widgets）
- FSM 状态显示
- 黑板数据显示：game_state、current_step、action_result
- 命令输入框
- 连接状态指示

### 1.2 需求缺口（根据 TheSeedJan11.md）

#### 缺失模块：
1. **游戏 Benchmark 套件** - 完全缺失
2. **Agent Benchmark 实时指标**：
   - token / 分钟
   - LLM calls / 分钟
   - Agent 任务数量
   - action 数量
   - 执行内容量
   - 失败率
   - 恢复率
3. **Trace 回放功能**：
   - 游戏实时日志滚动显示
   - FSM 转移历史记录
   - 当前 action 状态详细视图
4. **Memory 监控面板**：
   - 存储内容展示
   - 每次查询命中内容
   - 新增内容追踪

### 1.3 可用资源
- ✓ makepad-skills 已安装（包含丰富的 patterns 和组件模板）
- ✓ makepad-component 参考（Button, Progress, Slider 等组件）
- ✓ 现有的 WebSocket 通信框架
- ✓ Serde JSON 序列化支持

---

## 二、架构设计

### 2.1 UI 布局结构

采用 **多面板布局**（参考 makepad-skills 的 Dock-Based Studio Layout）：

```
┌─────────────────────────────────────────────────────────┐
│  Top Bar (状态、连接指示、主题切换)                       │
├──────────┬──────────────────────────────────┬──────────┤
│          │                                  │          │
│  Left    │     Center (Tab View)            │  Right   │
│  Panel   │     ┌──────────────────────┐     │  Panel   │
│          │     │ □ Agent Benchmark    │     │          │
│  FSM     │     │ □ Trace & Logs       │     │  Real    │
│  State   │     │ □ Memory Monitor     │     │  Time    │
│  Tree    │     │ □ Game Benchmark     │     │  Metrics │
│          │     └──────────────────────┘     │          │
│          │                                  │          │
│          │  [内容区域]                       │          │
└──────────┴──────────────────────────────────┴──────────┘
│  Bottom Bar (命令输入框 + 快捷操作)                       │
└─────────────────────────────────────────────────────────┘
```

### 2.2 数据模型扩展

需要扩展 `ws_client.rs` 中的消息类型：

```rust
// 新增消息类型
pub enum DashboardMessage {
    Init(FsmStatePayload),           // 现有
    Update(FsmStatePayload),         // 现有
    Log(LogPayload),                 // 现有

    // 新增
    AgentMetrics(AgentMetricsPayload),
    GameMetrics(GameMetricsPayload),
    MemoryUpdate(MemoryPayload),
    TraceEvent(TraceEventPayload),
}

// Agent 性能指标
pub struct AgentMetricsPayload {
    pub tokens_per_min: f64,
    pub llm_calls_per_min: f64,
    pub active_tasks: usize,
    pub total_actions: usize,
    pub execution_volume: usize,
    pub failure_rate: f64,
    pub recovery_rate: f64,
    pub timestamp: u64,
}

// 游戏性能指标
pub struct GameMetricsPayload {
    pub fps: f64,
    pub frame_time_ms: f64,
    pub tick_rate: f64,
    pub entity_count: usize,
}

// Memory 监控
pub struct MemoryPayload {
    pub total_entries: usize,
    pub recent_queries: Vec<MemoryQuery>,
    pub recent_additions: Vec<MemoryEntry>,
}

// Trace 事件
pub struct TraceEventPayload {
    pub timestamp: u64,
    pub event_type: String,  // "fsm_transition" | "action_start" | "action_end"
    pub from_state: Option<String>,
    pub to_state: Option<String>,
    pub details: serde_json::Value,
}
```

### 2.3 组件划分

#### 核心组件：
1. **MetricsCard** - 实时指标卡片（显示数值 + 趋势）
2. **LogViewer** - 可滚动的日志查看器
3. **HistoryTimeline** - FSM 转移历史时间线
4. **MemoryTable** - Memory 内容表格
5. **TabView** - 可切换的标签页组件
6. **MiniChart** - 简单的折线图（sparkline）

---

## 三、实施计划（分 5 个阶段）

### 阶段 1：重构 UI 布局 + 多面板结构

**目标**：建立可扩展的多面板布局框架

**任务**：
- [ ] 1.1 创建 `components/` 目录，拆分组件
- [ ] 1.2 实现 `LeftPanel`（FSM 状态树）
- [ ] 1.3 实现 `RightPanel`（实时指标卡片占位）
- [ ] 1.4 实现 `TabView` 组件（中央区域）
- [ ] 1.5 重构 `app.rs`，使用新的布局结构

**交付物**：
- 清晰的三栏布局
- 可切换的 Tab 页面框架

---

### 阶段 2：实现 Agent Benchmark 面板

**目标**：显示实时 Agent 性能指标

**任务**：
- [ ] 2.1 扩展 `ws_client.rs` 添加 `AgentMetricsPayload`
- [ ] 2.2 实现 `MetricsCard` 组件（数值 + 单位 + 趋势箭头）
- [ ] 2.3 实现 `AgentBenchmarkPanel`：
  - token/分钟
  - LLM calls/分钟
  - 任务数量
  - action 数量
  - 失败率/恢复率
- [ ] 2.4 添加历史数据缓存（最近 100 条记录）
- [ ] 2.5 可选：添加 MiniChart 显示趋势曲线

**交付物**：
- Agent Benchmark Tab 页面
- 实时指标更新

---

### 阶段 3：实现 Trace 回放面板

**目标**：显示游戏日志、FSM 历史、Action 状态

**任务**：
- [ ] 3.1 扩展 `ws_client.rs` 添加 `TraceEventPayload`
- [ ] 3.2 实现 `LogViewer` 组件：
  - 自动滚动
  - 日志级别颜色区分
  - 过滤功能（INFO/WARN/ERROR）
- [ ] 3.3 实现 `HistoryTimeline` 组件：
  - FSM 状态转移历史（时间戳 + from → to）
  - 最近 50 条记录
- [ ] 3.4 实现 `ActionDetailView`：
  - 当前 action 名称
  - 参数
  - 执行状态
  - 结果
- [ ] 3.5 整合为 `TracePanel`

**交付物**：
- Trace & Logs Tab 页面
- 日志实时滚动
- FSM 历史记录显示

---

### 阶段 4：实现 Memory 监控面板

**目标**：监控 Agent Memory 的存储和查询

**任务**：
- [ ] 4.1 扩展 `ws_client.rs` 添加 `MemoryPayload`
- [ ] 4.2 实现 `MemoryTable` 组件：
  - 显示存储条目（key + timestamp）
  - 查询命中高亮
  - 新增内容标记
- [ ] 4.3 实现 `MemoryStatsCard`：
  - 总存储量
  - 查询命中率
  - 最近查询列表
- [ ] 4.4 整合为 `MemoryMonitorPanel`

**交付物**：
- Memory Monitor Tab 页面
- Memory 内容展示

---

### 阶段 5：实现游戏 Benchmark 套件

**目标**：显示游戏运行时性能指标

**任务**：
- [ ] 5.1 扩展 `ws_client.rs` 添加 `GameMetricsPayload`
- [ ] 5.2 实现 `GameBenchmarkPanel`：
  - FPS
  - 帧时间（ms）
  - Tick Rate
  - 实体数量
- [ ] 5.3 可选：添加性能图表（FPS 曲线）

**交付物**：
- Game Benchmark Tab 页面
- 游戏性能实时监控

---

## 四、技术要点

### 4.1 Makepad Patterns 应用

参考已安装的 makepad-skills：

| 需求 | 使用的 Pattern |
|------|----------------|
| 多面板布局 | `04-patterns/_base/15-dock-studio-layout.md` |
| Tab 切换 | `04-patterns/_base/07-radio-navigation.md` |
| 实时数据更新 | `04-patterns/_base/08-async-loading.md` |
| 日志滚动 | `02-components` + ScrollView |
| 数据缓存 | `04-patterns/_base/05-lru-view-cache.md` |
| 状态管理 | `04-patterns/_base/10-state-machine.md` |

### 4.2 数据流

```
WebSocket Server (Python)
    ↓ (JSON over WS)
ws_client.rs (tungstenite)
    ↓ (DashboardMessage enum)
app.rs (handle_dashboard_message)
    ↓ (set_text / update state)
UI Components (live_design! widgets)
```

### 4.3 性能优化

- 使用环形缓冲区限制历史数据（避免内存无限增长）
- 日志最多保留 1000 条
- 指标历史最多保留 500 个数据点
- 使用 `Timer` 批量更新 UI（避免每次消息都 redraw）

---

## 五、里程碑

| 阶段 | 交付时间 | 完成标志 |
|------|---------|---------|
| 阶段 1 | Day 1-2 | 三栏布局 + Tab 切换正常 |
| 阶段 2 | Day 3 | Agent Benchmark 面板显示实时数据 |
| 阶段 3 | Day 4-5 | Trace 面板 + 日志滚动 + FSM 历史 |
| 阶段 4 | Day 6 | Memory 监控面板 |
| 阶段 5 | Day 7 | 游戏 Benchmark 面板 |

**总计：约 7 天完成所有功能**

---

## 六、后续优化方向

1. **数据导出**：支持导出日志、指标为 CSV/JSON
2. **图表可视化**：使用自定义 shader 绘制性能曲线
3. **过滤和搜索**：日志关键词搜索、FSM 状态过滤
4. **回放功能**：基于 Trace 数据的事件回放
5. **主题切换**：支持 Light/Dark 主题
6. **性能优化**：虚拟滚动（大量日志时）

---

## 七、依赖和环境

### 7.1 已有依赖
```toml
[dependencies]
makepad-widgets = { git = "https://github.com/makepad/makepad", branch = "rik" }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
tungstenite = { version = "0.20", features = ["rustls-tls-native-roots"] }
url = "2.4"
crossbeam-channel = "0.5"
anyhow = "1.0"
```

### 7.2 可能需要添加
```toml
# 如果需要更高级的时间处理
chrono = "0.4"

# 如果需要环形缓冲区
ringbuffer = "0.15"
```

---

## 八、测试策略

1. **单元测试**：测试消息解析（serde 反序列化）
2. **集成测试**：使用 mock WebSocket 数据测试 UI 更新
3. **手动测试**：连接真实的 THE-Seed 后端，验证所有面板功能

---

## 总结

本计划将 Dashboard 从当前的简陋原型升级为功能完整的监控工具，覆盖：
- ✓ Agent 性能监控
- ✓ 游戏性能监控
- ✓ Trace 回放和日志
- ✓ Memory 监控

采用模块化设计，每个阶段独立交付，便于迭代和测试。
