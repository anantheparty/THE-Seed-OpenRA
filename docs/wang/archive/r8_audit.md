# E2E R8 Deep Audit Report

**Session**: `session-20260405T141437Z`
**Duration**: 17m21s | **Tasks**: 24 (21 succeeded, 3 partial, 0 failed)
**LLM**: DeepSeek-Chat, 345 calls, 100% success | **Errors**: 0

---

## vs R7 改善

| 指标 | R7 | R8 | 改善 |
|------|----|----|------|
| ERROR | 65 | **0** | 完全消除 |
| 任务成功率 | 39% (14/36) | **87.5%** (21/24) | +48% |
| LLM成功率 | 100% (误标为失败) | **100%** (真正0失败) | 归因修正 |
| defend_base洪泛 | 13个 | **1个** (cooldown生效) | 消除 |
| 探索覆盖 | 77% | **85%** | +8% |

---

## P0 — Wang已修

### B1: 探索grid layout误判 — scout被送回基地
- **文件**: `experts/recon.py:135`
- **根因**: `_choose_grid_layout` 在高探索率时 row_major/col_major 平分，`>=`默认row_major(错误)
- **影响**: `_fallback_unexplored_centroid`算出目标(100,4)而非(4,100)，scout被送回基地方向
- **用户症状**: "探索就是不去左下角，走着走着就回头了"
- **修复**: `>=`→`>` (col_major为OpenRA默认); 两个调用点改为传base+scout位置

### B2: NLU `_looks_like_implicit_produce` catch-all
- **文件**: `adjutant/runtime_nlu.py:439`
- **根因**: `re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{1,8}")` 匹配任何1-8字中文
- **影响**: "断电了"→produce, "找到敌方基地"→produce(fact)
- **修复**: 替换为建筑/单位关键词正向匹配列表

---

## P1 — 待修

### B3: LLM分类缺"info"类型
- "找不到地方基地吗？就在左下角" → query → 丢弃
- "就在剩下的14%里啊" → query → 丢弃
- "发现敌人，被打了" → query → 丢弃
- **3条关键情报被系统忽略**
- **建议**: 新增info分类类型，路由到最相关的活跃任务

### B4: query_world死循环
- t_dd5942a9: 163次LLM调用中133次 query_world(enemy_bases)→空
- 单任务消耗3.76M prompt tokens (73% of session)
- **建议**: SYSTEM_PROMPT加规则 — query_world连续3次空结果后停止，等待signal

### B5: 语义重叠任务未合并
- "找到敌方基地" + "深度探索敌方基地" 完全并行312秒
- **建议**: 创建任务时检查是否有语义相同的活跃任务

### B6: 采矿车auto_place失败63次
- 所有harv生产后auto_place反复失败

### B7: 音频系统未初始化
- 0条voice/ws事件，ws_enabled=true但无连接
