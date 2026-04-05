# E2E Round 5 分析 — Xi 部分

Session: session-20260404T192621Z | 17 tasks | ~6 min | deepseek-chat

---

## 0. Benchmark 性能统计

| 指标 | count | avg_ms | p95_ms | max_ms | total_ms |
|---|---|---|---|---|---|
| llm_call | 144 | 2742 | 4525 | 6794 | 394,937 |
| job_tick | 3161 | 30 | 150 | 3030 | 95,241 |
| world_refresh | 2899 | 30 | 149 | 3028 | 87,836 |
| tool_exec | 3236 | 0.11 | 0.45 | 89 | 352 |
| expert_logic | 514 | 0.36 | 1.19 | 14 | 184 |

**亮点**：
- 144 次 LLM 调用，0 失败（R4 有 8 次 BadRequestError）
- 平均 2.7s/call（可接受），p95 4.5s
- R4-2 修复效果显著：is_explored grid 删除后 context 大幅缩小

---

## 1. 侦察失败分析

### t_09ae7475 "3步兵探索地图" — FAILED, 31s, 1.5%→2.6%

**LLM 决策流程**：
1. 识别到已有 ReconExpert job（Adjutant 规则路由创建），bootstrap 挂载
2. 查询 my_actors → 发现 3 个步兵（135/136/137），当前 job 只用 1 个
3. patch_job 增加 scout_count=3 ✅ 合理
4. 等待探索 → 收到 progress signal → 继续等待
5. 收到 task_complete(result=partial) → bootstrap 自动闭环为 FAILED

**问题根因**：ReconJob 30s 超时（`_max_explore_time_s`?），3 个步兵只探索了 1.5%。不是 LLM 决策问题，是 **ReconJob 超时过短** + **探索算法效率低**。

### t_5c80d763 "深度探索地图" — FAILED, 51s, 4.1%→7.8%

**LLM 决策流程（16 wakes, 多轮）**：
1. 挂载已有 ReconExpert job
2. 发现探索慢 → 生产更多步兵（增加侦察力量）✅
3. 决定建雷达增强视野 ✅ 非常合理的判断
4. 雷达建完但发现低电力 → 建电厂 ✅
5. 重启侦察 → 探索度增加到 ~8%
6. 最终超时失败

**LLM 表现**：优秀。自主判断需要雷达、补电、增兵，策略完全正确。失败原因是 **ReconJob 超时太短**，而非决策质量。

**建议**：
- ReconJob `_max_explore_time_s` 从 30s 提升到 120-180s
- 或改为基于 explored_gain_rate 判断：如果近 30s 探索度增量 > 0.5%，继续

---

## 2. "所有部队，深度探索" (t_2e54072e) — 73.8s, 25 LLM calls, 未完成

**LLM 决策（25 wakes, 23 tool calls）**：
- 9 次 scout_map：请求 10→20→30→14→15→15→20→21→10 = **155 个侦察兵**
- 9 次 patch_job：修改已有 job 参数（大部分无效，目标 job 无资源）
- 4 次 query_world + 1 次 send_task_message

**10 个 ReconExpert Job**（含 rule router 自动创建的第 1 个）：
- **4 个完成**（partial）：前 4 个 job 获得 74 次 resource_granted
- **6 个零资源**：jobs 5-10 创建后从未收到任何单位，立即 resource_lost
- **59 次 job_paused / 59 次 job_resumed** — 大量 pause/resume 抖动

**探索进度**：14.1% → 43.7%（+29.6%），前 4 个 job 有效，但 task 从未调用 complete_task

**问题**：
1. **过度创建 job**：10 个并行 ReconJob 中 6 个从未获得资源 — 纯粹浪费
2. **patch_job 空转**：9 次 patch_job 中大部分目标 job 无资源，修改参数无实际效果
3. **无终止条件**：43.7% 探索度 + 21 个闲置单位 + 全部 job waiting/failed = 死循环
4. **LLM 不理解资源竞争**：不断创建新 job 但不知道单位已被旧 job 占用

**根因**：
- LLM 无法观测到 per-job 实际分配的 actor 数量（context 缺少此信息）
- SYSTEM_PROMPT 无同类 job 并行上限指导
- 缺少 "active_resource_grants_per_job" 信息暴露给 context

---

## 3. "继续发展经济" (t_8d1bfa62) — SUCCEEDED

**LLM 决策（10 wakes, 16 LLM calls）**：
1. query_world → 评估当前状态
2. produce_units(weap, Building) — 造车厂 ✅
3. produce_units(powr, Building) — 补电 ✅
4. send_task_message(info) — 通知玩家
5. 等待建造完成
6. produce_units(harv, Vehicle) — 造第二辆矿车 ✅
7. produce_units(proc, Building) — 造第二个矿场 ✅
8. produce_units(powr, Building) — 再补电 ✅
9. complete_task(succeeded)

**评价**：策略非常合理。按 车厂→矿车→矿场→电厂 的经济发展路线执行，符合 RA 经济逻辑。总结也准确。

---

## 4. "爆兵" (t_b6bf1718) — SUCCEEDED（但实际产出远低于预期）

**LLM 决策（2 wakes, 19 LLM calls, 13 jobs created）**：
1. query_world → 评估产能
2. e1×10(repeat) + 2tnk×5(repeat) — 双产线意图正确，但 2tnk 立即 cannot_produce
3. e2×5 — **FAILED**: "缺少前置建筑（兵营）" ⚠️ 兵营实际存在，**prerequisite 检查 bug**
4. jeep×3 — **FAILED**: cannot_produce + utf-8 codec error
5. e3×5 — 成功 ✅
6. 发现电力不足 → powr×2 ✅
7. 重试 2tnk×3, jeep×2, 2tnk×5 — 全部失败（cannot_produce / REQUEST_ID_MISMATCH）
8. 继续 e1×5, e1×10, e2×5 — e1 成功，e2 仍 cannot_produce
9. complete_task(succeeded) — 在 51 单位时自评成功

**实际产出 vs 下单量**：
- ✅ e1: **16 个**（1+10+5 produced），下单 26
- ✅ e3: **5 个**
- ❌ 2tnk: **0 个**（下单 13，3 次尝试全部 cannot_produce）
- ❌ jeep: **0 个**（下单 5，2 次尝试全部失败）
- ❌ e2: **0 个**（下单 10，2 次尝试全部 cannot_produce）
- ✅ powr: 2 个
- **实际新增 21 个步兵**，总部队 15→51（含预存单位），Job 成功率 4/13（31%）

**新发现 bug**：
- **R5-4**: e2 cannot_produce "缺少前置建筑（兵营）" — 兵营存在，prerequisite 检查逻辑有 bug
- **R5-5**: 2tnk/jeep 持续 cannot_produce — 可能缺高级前置，但 LLM 未诊断就反复重试 3 次
- **R5-6**: GameAPI utf-8 codec error + REQUEST_ID_MISMATCH — 并发队列操作触发运行时 bug

**评价**：意图正确（多兵种、双产线、补电），但实际效果差 — 零车辆产出，31% job 成功率。LLM 对 cannot_produce 缺乏诊断能力，自评 summary 声称"坦克、吉普车"在生产，与事实不符。

---

## 5. 细节问题

### t_9340bc8e + t_1d488453：重复建造 weap — 更严重的问题

- **t_9340bc8e**（label 009, 13s, 3 LLM calls）：produce_units(weap) → succeeded
- **t_1d488453**（label 010, 10s, 4 LLM calls）：query_world → produce_units(weap) → query_world → succeeded

**深度发现**：两个 task 的 EconomyExpert job 都是 **ABORTED**（非 succeeded）。只建了 1 个车厂，但两个 task 都通过 `query_world` 看到 `war_factory_count=1`，各自抢先 `complete_task(succeeded)`。Job 被 Kernel 在 task 结束时强制 abort。

**问题**：
1. **Job aborted 但 task 声称 succeeded** — LLM 绕过 job 生命周期，直接读 game state 判定完成（与 R5-2 同类问题）
2. 两个 task 竞争同一 Building queue，只产出 1 个 weap 但 2 个 task 都标记成功
3. Adjutant 在 3 秒内创建了 2 个相同目标的 task

### t_393d4d53 "大电" — SUCCEEDED（边界时序巧合）

**LLM 决策（2 LLM calls, 5s）**：
1. query_world(economy_status) → 电力 300/260 充足
2. complete_task(succeeded, "核电厂建造任务已由EconomyExpert接管并正在执行中")

**深度发现**：Job j_e33008d4（Kernel NLU 自动派发）实际在 19:30:59 succeeded（expert_signal "生产完成 1/1: 核电站"），LLM 响应在 19:30:59.568 返回。Job 恰好在 LLM 推理期间完成，所以结果碰巧正确。

**问题**：LLM 推理文本说 "任务正在运行中...需要等待"，但同一轮就调了 complete_task — 自相矛盾。Summary 说"正在执行中"而非"已完成"。如果 job 晚 1 秒完成就会是真正的假 succeeded。

### t_7ee73a6c "机场" — SUCCEEDED（自修复重复 job）

**LLM 决策（3 LLM calls, 9s）**：
1. produce_units(afld, Building) — 创建第二个 job（j_b0a0d7ff）
2. abort_job(j_b0a0d7ff) — 发现已有 j_8e145eb8（Kernel 自动派发），主动清理重复
3. complete_task(succeeded) — j_8e145eb8 已 succeeded

**评价**：Kernel 在 task 创建时自动派发了 j_8e145eb8，LLM 不知情又创建了一个。但 LLM 在下一轮发现两个 job 后正确 abort 了重复的。最终结果正确，但暴露了 **bootstrap 与 LLM 重复创建 job** 的系统性问题。

---

## 6. 发现汇总

### 严重问题

| ID | 问题 | 根因 | 建议修复 |
|---|---|---|---|
| R5-1 | ReconJob 30s 超时太短，探索度 <10% 就失败 | `_max_explore_time_s` 硬编码过小 | 延长到 120-180s，或改为基于 gain_rate 动态判断 |
| R5-2 | LLM 绕过 job 生命周期判定完成 — 读 game state 而非等 job succeeded | SYSTEM_PROMPT 完成判定规则未阻止（weap 两个 task 都这样） | handle_complete_task 硬拒绝无 succeeded job 的 complete_task |
| R5-3 | 多 scout_map 并行抢资源 → 6/10 job 零资源死循环 | context 缺少 per-job actor 分配信息 | 暴露 resource_grants_per_job + 限制同类 job 并行数 |
| R5-4 | e2 cannot_produce "缺少兵营" — 兵营实际存在 | prerequisite 检查逻辑 bug（knowledge.py?） | 修复 prerequisite 检查 |
| R5-5 | 2tnk/jeep cannot_produce — LLM 重试 3 次不诊断 | 可能缺高级前置 + LLM 无诊断 prompt | 暴露具体缺少的前置条件 |
| R5-6 | GameAPI utf-8 codec error + REQUEST_ID_MISMATCH | 并发队列操作触发运行时 bug | 调查 GameAPI 并发安全性 |

### 次要问题

| ID | 问题 | 说明 |
|---|---|---|
| R5-7 | 重复建造 weap（2 个独立 task 竞争 1 个产出） | Adjutant 3 秒内创建 2 个相同目标 task |
| R5-8 | Kernel 自动派发 + LLM 重复创建 job（机场 task） | bootstrap 与 LLM 创建 job 的协调问题，LLM 能自修复 |
| R5-9 | "大电" summary 说"正在执行中"但标记 succeeded | LLM 推理文本与 tool call 自相矛盾（结果碰巧正确） |

### 正面发现

- **经济决策质量高**："继续发展经济" 5/5 job 全部成功，策略完全合理
- **自主问题解决**："深度探索" 中自主判断需要雷达+补电，策略正确
- **LLM 0 失败**：R4-2 修复后 context 大幅缩小，无一次 BadRequestError
- **探索有效率**：mass recon 前 4 个 job 将探索从 14% 推到 44%
- **LLM 自修复能力**：机场 task 中 LLM 发现重复 job 后主动 abort 清理
