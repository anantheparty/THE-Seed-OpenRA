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

## 2. "所有部队，深度探索" (t_2e54072e)

**LLM 决策**：
- 创建了 8+ 个 scout_map 调用，scout_count 从 10→20→30 疯狂递增
- 大量 patch_job 调用修改参数
- 总共 23 个 tool call
- **任务未完成**：session 结束时仍在运行（无 agent_stopped/task_completed）

**问题**：
1. **过度创建 job**：每次 scout_map 创建新 ReconExpert job，不是修改已有的。8+ 个并行 ReconJob 抢夺同一批 actor 资源
2. **scout_count 通胀**：30 个侦察兵 → 实际可能只有 ~20 个可用单位，资源不足导致 resource_lost 信号循环
3. **��终止条件**：LLM 不知道什么时候该停止创建新的 scout job

**根因**：
- SYSTEM_PROMPT 没有限制同类 job 并行数量的指导
- LLM 不理解 scout_count 和实际可用单位的关系
- 缺少 "探索目标已基本达成" 的判断信号

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

## 4. "爆兵" (t_b6bf1718) — SUCCEEDED

**LLM 决策（2 wakes, 19 LLM calls）**：
1. query_world → 评估产能
2. 批量下单：e1×10(步兵) + 2tnk×5(坦��) ✅ 同时利用两条产线
3. e2×5(掷弹兵) + jeep×3(吉普) — 补充兵种多样性 ✅
4. e3×5(火箭兵) — 反装甲 ✅
5. 发现电力不足 → powr×2 ✅
6. 持续追加：e1×5, 2tnk×3, jeep×2, 2tnk×5, e1×10, e2×5
7. complete_task(succeeded)

**产出统计**：
- 步兵线：e1×25, e2×10, e3×5 = 40 步兵
- 车辆线：2tnk×13, jeep×5 = 18 车辆
- 建筑：powr×2（补电）
- 总部队 15→51（+240%）

**评价**：出色。多兵种混编，识别电力瓶颈并解决，充分利用 Infantry + Vehicle 双产线。19 次 LLM call 都在有效推进。

---

## 5. 细节问题

### t_9340bc8e + t_1d488453：重复建造 2 个 weap

- **t_9340bc8e**：produce_units(weap) → succeeded（3 LLM calls）
- **t_1d488453**：query_world → produce_units(weap) → query_world → succeeded（4 LLM calls）

**问题**：两个独立 task 都建了 weap。**这不是 LLM bug** — 每个 TaskAgent 只看自己的 task，不知道另一个也在建 weap。Adjutant 应在创建 task 前检查 `other_active_tasks` 是否已有相同目标。

**但实际上**：2 个车厂在 RA 中是有用的（双产线加速车辆生产），所以也不算严重浪费。问题是玩家可能不想要 2 个。

### t_393d4d53 "大电" — SUCCEEDED 但可疑

**LLM 决策**：
1. query_world → 发现 apwr(大电) 已在 bootstrap EconomyExpert 中
2. 直接 complete_task(succeeded, "核电厂建造任务已由EconomyExpert接管并正在执行中")

**问题**：**假 succeeded**。LLM 看到 bootstrap 已创建 EconomyExpert job，就判断 "已接管" 并标记成功。但实际建造可能还没完成。这是 SYSTEM_PROMPT `完成判定` 规则的边界情况 — bootstrap job 正在运行不等于 succeeded。

**根因**：LLM 将 "job 正在运行" 误判为 "任务已完成"。需要在 prompt 中强调 "succeeded 要求 job status=succeeded，不能是 running"。

### t_7ee73a6c "机场" — SUCCEEDED

**LLM 决策**：
1. produce_units(afld, Building) — 造机场 ✅
2. abort_job — 中止了一个 job（可能是 bootstrap 创建的重复 job）
3. complete_task(succeeded)

**评价**：看起来正常。abort_job 可能是因为 bootstrap 和 LLM 都创建了 job，LLM 清理了重复的。3 次 LLM call 完成，高效。

---

## 6. 发现汇总

### 严重问题

| ID | 问题 | 根因 | 建议修复 |
|---|---|---|---|
| R5-1 | ReconJob 30s 超时太短，探索度 <10% 就失败 | `_max_explore_time_s` 硬编码过小 | 延长到 120-180s，或改为基于 gain_rate 动态判断 |
| R5-2 | "大电" 假 succeeded — job 还在 running 就标记完成 | LLM 将 "job 已启动" 误判为 "任务完成" | SYSTEM_PROMPT 强调 succeeded 要求至少一个 job status=succeeded |
| R5-3 | 多 scout_map 并行抢资源 → 无限循环 | LLM 不理解 resource 竞争，不知何时停止 | 限制同类 job 并行数 or 在 runtime_facts 暴露 active_job_count_by_expert |

### 次要问题

| ID | 问题 | 说明 |
|---|---|---|
| R5-4 | 重复建造 weap（2 个独立 task） | 多 task 协调问题，非单 task LLM bug |
| R5-5 | 机场 task 中 abort_job 清理重复 job | bootstrap 和 LLM 都创建了 job，不算 bug 但可优化 |

### 正面发现

- **经济决策质量高**："继续发展经济" 和 "爆兵" 策略完全合理
- **自主问题解决**："深度探索" 中自主判断需要雷达+补电，策略正确
- **LLM 0 失败**：R4-2 修复后 context 大幅缩小，无一次超限
- **爆兵效率**：2 wakes, 19 LLM calls 生产 58 个单位 + 2 电厂，决策密度极高
