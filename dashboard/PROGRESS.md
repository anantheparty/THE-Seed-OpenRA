# Dashboard 开发进度跟踪

**项目**: THE SEED - OpenRA Dashboard
**开始时间**: 2026-01-19
**当前状态**: 开发中

---

## 总体进度

| 阶段 | 状态 | 开始时间 | 完成时间 | 备注 |
|------|------|---------|---------|------|
| 准备阶段 | ✅ 完成 | 2026-01-19 | 2026-01-19 | makepad-skills安装，计划制定 |
| 阶段1: UI布局重构 | ✅ 完成 | 2026-01-19 | 2026-01-19 | 多面板结构 |
| 阶段2: Agent Benchmark | ⏳ 待开始 | - | - | 实时指标显示 |
| 阶段3: Trace回放 | ⏳ 待开始 | - | - | 日志+FSM历史 |
| 阶段4: Memory监控 | ⏳ 待开始 | - | - | Memory展示 |
| 阶段5: 游戏Benchmark | ⏳ 待开始 | - | - | 游戏性能指标 |

**整体完成度**: 1/5 阶段完成 (20%)

---

## 准备阶段 ✅

### 完成内容
- [x] 安装 makepad-skills 到项目
- [x] 探索 makepad-component 和 makepad-skills-demo 示例项目
- [x] 分析当前实现和需求差距
- [x] 制定详细开发计划 (DASHBOARD_PLAN.md)

### 资源准备
- ✅ makepad-skills 已安装到 `.claude/skills/`
- ✅ 参考项目已 clone 到 `/Users/kamico/work/theseed/makepad/`
- ✅ WebSocket 通信框架已就绪

---

## 阶段1: UI布局重构 ✅

**目标**: 建立可扩展的多面板布局框架

### 任务清单
- [x] 1.1 创建 `src/components/` 目录
- [x] 1.2 实现 `LeftPanel` 组件（FSM 状态显示）
- [x] 1.3 实现 `RightPanel` 组件（实时指标占位）
- [x] 1.4 实现 `TabView` 组件（中央标签页切换）
- [x] 1.5 重构 `app.rs` 使用新布局

### 进度详情
- 开始时间: 2026-01-19
- 完成时间: 2026-01-19
- 实际进度: 100%

### 交付物
- [x] 清晰的三栏布局
- [x] 可切换的 Tab 页面框架
- [x] 代码模块化拆分

### 实现细节
1. **组件结构**：
   - `left_panel.rs` - FSM状态、当前目标、进度显示
   - `right_panel.rs` - 连接状态、实时指标占位
   - `tab_view.rs` - 4个标签页（Agent Benchmark、Trace、Memory、Game State）

2. **UI布局**：
   - 顶部栏：标题 + 版本号
   - 主体：左面板(250px) + 中央TabView + 右面板(280px)
   - 底部栏：命令输入框 + 发送按钮

3. **标签页切换**：
   - 使用 Button 实现标签（TabButton / TabButtonActive）
   - 点击切换显示对应内容区域
   - 底部绿色边框标识当前选中标签

4. **编译状态**：
   - ✅ 编译成功
   - ⚠️ 6个警告（未使用的导入，不影响功能）

---

## 阶段2: Agent Benchmark ⏳

**目标**: 显示实时 Agent 性能指标

### 任务清单
- [ ] 2.1 扩展 `ws_client.rs` 数据模型
- [ ] 2.2 实现 `MetricsCard` 组件
- [ ] 2.3 实现 `AgentBenchmarkPanel`
- [ ] 2.4 添加历史数据缓存
- [ ] 2.5 可选：添加趋势图

---

## 阶段3: Trace回放 ⏳

**目标**: 显示游戏日志、FSM 历史、Action 状态

### 任务清单
- [ ] 3.1 扩展数据模型（TraceEvent）
- [ ] 3.2 实现 `LogViewer` 组件
- [ ] 3.3 实现 `HistoryTimeline` 组件
- [ ] 3.4 实现 `ActionDetailView` 组件
- [ ] 3.5 整合为 `TracePanel`

---

## 阶段4: Memory监控 ⏳

**目标**: 监控 Agent Memory 的存储和查询

### 任务清单
- [ ] 4.1 扩展数据模型（Memory）
- [ ] 4.2 实现 `MemoryTable` 组件
- [ ] 4.3 实现 `MemoryStatsCard` 组件
- [ ] 4.4 整合为 `MemoryMonitorPanel`

---

## 阶段5: 游戏Benchmark ⏳

**目标**: 显示游戏运行时性能指标

### 任务清单
- [ ] 5.1 扩展数据模型（GameMetrics）
- [ ] 5.2 实现 `GameBenchmarkPanel`
- [ ] 5.3 可选：性能图表

---

## 技术债务和问题

### 已知问题
_当前无_

### 待优化
- 数据导出功能
- 图表可视化增强
- 搜索和过滤功能
- 主题切换支持

---

## Git 提交历史

| 日期 | 提交 | 描述 |
|------|------|------|
| 2026-01-19 | `初始化` | 创建进度跟踪文档和开发计划 |
| 2026-01-19 | `阶段1完成` | 实现多面板布局和标签页切换 |

---

## 下一步行动

**当前焦点**: 阶段2 - Agent Benchmark 面板

**关键任务**:
1. 扩展 WebSocket 数据模型（AgentMetricsPayload）
2. 实现 MetricsCard 组件
3. 实现 Agent Benchmark 面板的7个实时指标
4. 添加历史数据缓存

**预计完成时间**: 1-2 小时
