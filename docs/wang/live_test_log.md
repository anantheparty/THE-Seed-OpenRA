# Live Integration Test Log (2026-03-31)

## 游戏状态
- 地图：128x128
- 经济：Cash=5000, Resources=0, Power=0/0
- 我方：1 辆基地车 (ID=130) at (12,90)
- 敌方：0（未发现）
- 阶段：游戏刚开始

## 系统状态
- 后端 main.py：运行中 (PID 44749)
- WS Server：ws://localhost:8765/ws 连通
- 前端 Vite：http://localhost:5173 运行中
- GameAPI：localhost:7445 连通

## 发现的问题

### Issue 1: Task raw_text 为空
- 发送"部署基地车"后，task_list 中 task 的 raw_text=""
- 可能是 Adjutant 或 Kernel 创建 Task 时没传入原始文本
- 严重性：高 — 影响 Task Agent 理解意图

### Issue 2: GameAPI 模型与 WorldModel 模型不匹配
- GameAPI Actor：actor_id, type, faction, position(Location), hppercent
- NormalizedActor：actor_id, name, display_name, owner, category, position(tuple), hp, hp_max
- GameAPIWorldSource 需要正确转换 faction→owner, type→name/category, hppercent→hp/hp_max
- 待验证：WorldModel 是否能正确读取并转换真实 GameAPI 数据

### Issue 3: 前端 WS 路径
- WS endpoint 是 ws://localhost:8765/ws（不是根路径）
- 前端 useWebSocket.js 需要确认连接的是 /ws 路径

### Issue 4: GameAPI TargetsQueryParam 用 faction 不用 owner
- API 用 faction 参数（"己方"/"中立"/etc），不是 design.md 的 owner(self/enemy/neutral)
- WorldModel 的 actor 查询 predicates 需要正确映射

### Issue 5: log_entry 刷屏
- WS 推送大量 log_entry（DEBUG 级别的 world_refresh + benchmark），淹没有用消息
- 应过滤 DEBUG 级别日志不推送到前端，或前端做级别过滤

### Issue 6: 起始阶段缺乏前置动作
- E2E 测试假设有单位/建筑可用
- 真实游戏需要先部署基地车 → 建电厂 → 建兵营 → 才能生产/侦察
- 需要一个"开局序列"或确保游戏已进入中期再测试

## 框架集成测试结果

### GameAPI 直接调用 — 正常 ✅
- deploy_units(MCV) → 建造厂 ✅
- produce(电厂/兵营/矿场/战车工厂) → 全部成功 ✅
- query_actor(faction='己方') → 正确返回 ✅
- 结论：GameAPI 底层完全正常

### WS 命令管道 — 问题严重 ❌

**T9 查询"战况如何"**：
- 发送 command_submit → 没有收到 query_response
- Adjutant 的查询分类+LLM 回答链路断了

**T2 "生产3辆坦克"**：
- 发送 command_submit → task_list 没有对应 task
- raw_text 全是空的

**defend_base 误触发**：
- 多个 defend_base task 被自动创建
- 游戏没有被攻击 — 事件检测误报

### 根因（已确认）

**Issue 7（新发现，P0）：LLM 不知道 Expert config schema**
- Qwen 调 start_job 时传错误参数：scan_mode/area/destination
- 正确应该是：search_region/target_type/target_position
- 根因：Task Agent system prompt 没有 Expert config schema 说明
- 修复：在 system prompt 中加入 5 种 ExpertConfig 的完整参数说明

**Issue 1+3 根因**：Adjutant 分类 LLM 失败时 fallback 到 command，但 error handling 不够 → xi 已修
**Issue 4 根因**：建筑 HP 微小波动触发 BASE_UNDER_ATTACK → yu 已修（加 5% 阈值 + 附近敌军检查）

## 已修复
- [x] BASE_UNDER_ATTACK 误触发 → yu 加 5% 阈值 + 附近敌军检查 (eca213a) ✅
- [x] Adjutant error handling → xi 加 try/except + fallback 规则 (415528e) ✅
- [x] ChatView 刷 task_update → xi 移除监听 (df77773) ✅
- [x] LLM 猜参数名 → xi 加 Expert config schema 到 system prompt (99c94af) ✅

## 验证结果
- defend_base 误触发：✅ 已修复
- WS 连接 + 推送：✅ 正常
- 命令处理（查询/命令）：❌ 未能验证 — 游戏在测试过程中失败（被敌人消灭）
- GameAPI 直接调用：✅ 正常（部署/建造全部成功）

## 第二轮测试（全修复后）

### 系统链路验证 ✅
- Adjutant 收到命令 ✅
- LLM 分类成功（command/query 都能识别）✅
- Task 创建成功（raw_text 正确）✅
- Task Agent tool_use 调用成功（query_world 返回真实数据）✅
- GameAPI 长连接工作 ✅

### 仍存在的问题
- **LLM 响应慢**：分类 ~2s + 查询 ~7s = 总共 ~18s。需要在前端显示"正在思考..."
- **BASE_UNDER_ATTACK 仍触发**：可能是 yu 修复未加载到当前实例，或检测逻辑仍有边界情况
- **查询回答未在测试中捕获**：因超时设置不够长

## 第三轮测试

### 成功 ✅
- 查询"战况如何" → 副官完整中文战况简报 ✅
- Task 创建 raw_text 正确 ✅
- LLM 正确选 DeployExpert（不再选 ReconExpert）✅
- LLM 正确选 ReconExpert 做侦察 ✅
- Category 判断修复生效（建筑=building）✅
- BASE_UNDER_ATTACK 正确触发 + defend_base 创建 ✅
- 0 后端错误 ✅
- defend_base 无误触发 ✅

### 仍存在的问题
- **Kernel 把建筑分配给 ReconJob** — actor:145（发电厂）被分配给侦察任务。资源匹配需排除 building/static
- **defend_base LLM 响应太慢** — 敌人已经在打了，LLM 还要 5-10s 思考。自动防御应该有默认快速行为
- **部署基地车失败** — 因为之前已经手动部署了（无 MCV），LLM 选了建造厂尝试 deploy（静默失败）

## 第四轮测试（全修复后 Live E2E）

| 测试 | 结果 | 详情 |
|---|---|---|
| T5 部署基地车 | ✅ | LLM 正确选 DeployExpert，MCV→建造厂 |
| T9 查询战况 | ✅ | 副官完整中文回答（经济/军事/任务/地图） |
| T1 探索地图 | ✅ 逻辑正确 | ReconJob 创建成功，waiting（无空闲可移动单位）— 正确行为 |
| T2 生产坦克 | ✅ 逻辑正确 | EconomyJob 创建成功，waiting（战车工厂未建好）— 正确行为 |
| T7 约束 | ⚠️ | LLM 当成 managed task 而非 create_constraint — 需优化 prompt |
| 0 ERROR | ✅ | 无后端错误 |

### 验证的系统行为
- LLM 正确选择 Expert 类型 ✅
- Kernel 资源匹配排除建筑 ✅
- 资源不足 → Job waiting + signal ✅
- 查询 → Adjutant 直接 LLM 回答 ✅
- ECONOMY_SURPLUS 主动通知 ✅
- 0 defend_base 误触发 ✅

### 待验证
- [ ] 有可移动单位后 ReconJob 是否自动恢复执行
- [ ] 战车工厂建好后 EconomyJob 是否自动恢复生产
- [ ] T7 "别追太远" → create_constraint（prompt 优化）
- [ ] T3 移动/T4 进攻/T6 包围（需要有战斗单位）
- [ ] defend_base 即时反射防御是否真的工作

## 已知环境问题
- 游戏失败后 GameAPI 断连 → 后端持续报 Connection Refused
- 需要 OpenRA 重启机制（yu 调研结论：进程级重启 + baseline save）
- 当前没有自动化重启游戏的工具
