# E2E R7 Deep Audit Report

**Session**: `session-20260405T103013Z`
**Duration**: 11m42s (10:30:13 ~ 10:41:55 UTC)
**LLM**: DeepSeek-Chat | **Tasks**: 36 (14 succeeded, 3 partial, 19 failed)
**Records**: 35,247 | **LLM calls**: 252 (0 LLM failures, 65 wake_cycle crashes)

---

## Timeline

```
10:30-10:37  正常阶段: 14个任务, 大部分成功
10:37:20     NormalizedActor.location bug触发 — 之后所有新任务必crash
10:37-10:41  瘫痪阶段: 22个任务全失败, 包括13个defend_base
10:41:53     游戏服务器断连(基地被打爆)
```

---

## P0 — 致命Bug (Wang已修)

### B1: `NormalizedActor.location` AttributeError
- **文件**: `world_model/core.py:631,652`
- **原因**: `a.location` 应为 `a.position` — 一行typo
- **影响**: 65个ERROR，22个任务死亡，防御系统完全瘫痪
- **状态**: **已修** (Wang, `.location` → `.position`)

### B2: wake_cycle crash被错误归因为"LLM失败"
- 19个failed任务summary写"LLM连续失败3次"，但LLM 252/252全部成功
- 实际crash在LLM调用之前的context构建阶段
- **状态**: **已修** (Wang, `_last_llm_error`现在记录wake_cycle_crash类型)

### B3: EconomyExpert阵营判断逻辑错误
- `faction_restriction_for("4tnk")` 返回 `"soviet"` (truthy) → 立即FAILED
- 但玩家就是Soviet！`cannot_produce`是缺前置，不是阵营不匹配
- **状态**: **已修** (Wang, 现在检查faction_req是否匹配player_faction)

### B4: runtime_facts缺少建筑计数
- 只有powr/barr/weap/proc/dome计数
- **缺少**: stek(科技中心), fix(维修厂) → 两个任务各建了一个stek和fix
- **状态**: **已修** (Wang, 新增tech_center_count, repair_facility_count)

---

## P1 — 严重设计缺陷 (待修)

### B5: defend_base任务无去重
- 1分钟内13个相同defend_base任务
- 每个BASE_UNDER_ATTACK事件创建新任务，无cooldown/合并
- **建议**: Kernel层加defend_base去重，60秒内只允许1个活跃防御任务

### B6: 玩家输入丢失
- "我就是苏联" → classified as reply(target=null) → 丢弃
- "敌人在左下角" → classified as reply(target=null) → 丢弃
- **根因**: 玩家回应屏幕通知(task_info/warning)，但系统只支持回应task_question
- **建议**: reply分类为null时，fallback到command处理；或将信息性回复注入最近活跃任务

### B7: NLU误路由
- "找到敌人基地" → produce(fact), conf=0.517 → 应为recon
- "敌人基地在左下角，侦查" → "敌人基地在左下角"被拆为produce命令
- "2矿场，2矿场" → intent=attack, conf=0.62
- **根因**: 低置信度无gate，composite不区分信息性/命令性子句
- **建议**: conf<0.7时fallback到LLM routing

### B8: composite拆分bug
- "补全到4车间和4兵营" → 创建2个相同任务(都是完整原文)
- 应拆为"4车间"和"4兵营"两个不同任务
- **建议**: composite拆分时使用各step的source_text而非整体input

### B9: other_active_tasks信息不足
- 只有{label, raw_text, status}
- 不包含：正在建造什么、正在生产什么、有哪些running jobs
- **结果**: 5个任务独立反应低电 → 9个电厂(实际需要3-4个)
- **建议**: 加入current_jobs摘要(expert_type + config要点)

### B10: 探索算法无法到达远角
- SW象限只有30.9%探索，其余>86%
- random-ray算法在已探索区域找不到目标时scout停止移动
- `search_region`只影响初始角度，不约束实际移动方向
- `enemy_half`硬编码为northeast，但敌人在southwest
- **建议**: 
  1. fallback机制: 当ray找不到目标时，直接向最大未探索连通区域中心移动
  2. search_region应约束目标选择范围
  3. enemy_half应根据己方基地位置动态推断

### B11: Job churn破坏探索进度
- 探索任务创建22个ReconJob，每个新job重置scout状态
- scout积累的waypoint链、已访问记录全部丢失
- **建议**: LLM不应反复创建新scout_map job，而是patch现有job参数

---

## P2 — 效率问题

### B12: 25%的LLM调用空转
- 64/252次只返回"wait"文本，无tool call
- t_e4a65aa6(找敌人基地) 68%空转(36/53)
- **建议**: smart_wake检测到"只有timer/review触发、无新signal/event"时跳过LLM

### B13: 任务scope creep
- "探索地图"任务建了雷达、电厂、步兵 — 远超名义scope
- SYSTEM_PROMPT说"不要扩展到通用经济"但LLM因ReconExpert信号("缺少雷达支撑")忽略了
- **建议**: ReconExpert不应建议建造建筑，应通过signal上报"缺少雷达"让LLM决定是否建造

### B14: world_refresh_slow
- 797次超过100ms阈值(18%)，avg=181ms
- T-R6-7 bitpack应该大幅改善此问题 — R8验证

---

## 修复分配

### Wang已修 (本轮):
- [x] B1: NormalizedActor.location → .position
- [x] B2: wake_cycle crash正确归因
- [x] B3: 阵营判断逻辑(faction_req匹配player_faction时不fail)
- [x] B4: 新增tech_center_count, repair_facility_count

### 分配给Xi:
- T-R7-1: B5 defend_base去重(Kernel层，60s cooldown)
- T-R7-2: B6 reply(null) fallback到command
- T-R7-3: B7 NLU conf<0.7时fallback到LLM routing
- T-R7-4: B8 composite拆分使用step source_text
- T-R7-5: B9 other_active_tasks加入current_jobs摘要
- T-R7-6: B10 探索fallback + search_region约束 + enemy_half动态推断
- T-R7-7: B11 限制scout_map job创建频率(复用现有job)

---

## 统计附录

| 指标 | 值 |
|------|-----|
| LLM调用 | 252次, 0失败 |
| prompt tokens | 2,564,544 (avg 10,177/call) |
| completion tokens | 28,525 (avg 113/call) |
| LLM avg latency | 3,467ms, p95=6,261ms |
| wake_cycle_error | 65次 (全部同一bug) |
| world_refresh_slow | 797次, avg=181ms |
| defend_base tasks | 13个, 全部crash |
| 探索最终覆盖 | 77.4% (SW象限30.9%) |
| 电厂建造 | 9个 (需要3-4个) |
| 科技中心 | 2个 (需要1个) |
| 维修厂 | 2个 (需要1个) |
