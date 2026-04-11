# Capability / Knowledge / Buildability 下一最小 Slice 审计

范围：`experts/knowledge.py`、`openra_state/data/dataset.py`、`world_model/core.py`、`unit_registry.py`、`task_agent/context.py`

## 结论

当前 `buildability / roster / prerequisite` 至少有三套真值：

1. `unit_registry.py`
   - 从 OpenRA YAML 读取原始 metadata / alias / raw prerequisites
   - 更接近长期 authoritative source
2. `openra_state/data/dataset.py`
   - 当前 `world_model/core.py::_derive_buildable_units()` 实际使用的 buildability 数据
   - 已经是“手工归一化后的简化 tech tree”
3. `experts/knowledge.py` + `task_agent/context.py`
   - 一个在给 Expert blocker / recommendation 用
   - 一个在给 Capability 过滤“demo-safe roster”用
   - 都在再次手写 roster / prerequisite 规则

这导致 Capability 看到的“可造什么”“缺什么前置”“本局合法 roster”并不来自同一处。

---

## 1) 当前哪些不一致

### A. `UnitRegistry` 与 `dataset.py` 的 prerequisite / faction 不一致

- `PROC`
  - `UnitRegistry`: `['anypower', '~techlevel.infonly']`
  - `dataset.py`: `['fact']`
  - 结果：`world_model` 当前会把 `proc` 当成“有建造厂即可造”，而不是“先有 power / anypower”

- `HARV`
  - `UnitRegistry`: `['proc', '~techlevel.infonly']`
  - `dataset.py`: `['proc', 'weap']`
  - 结果：buildability 与真实 YAML 语义不一致

- `FTRK`
  - `UnitRegistry`: `['~vehicles.soviet', '~techlevel.low']`
  - `dataset.py`: `['weap']`
  - 结果：`dataset` 用了简化版 prerequisite，`registry` 保留了更真实但未归一化的 prerequisites

- `MIG`
  - `UnitRegistry`: `['~afld', 'stek', '~techlevel.high']`
  - `dataset.py`: `['afld']`
  - 结果：Capability roster 里把 `mig` 当作合法目标，但底层简化树和 YAML 树都没统一

- `E1 / E3`
  - `UnitRegistry`: `faction='any'`, prereq `['~barracks', ...]`
  - `dataset.py`: 最终被后注册的 Soviet 版本覆盖，`faction='Soviet'`, prereq `['barr']`
  - 结果：`dataset` 丢失了“任意 barracks 均可”的语义

### B. `experts/knowledge.py` 与 `dataset.py` 的 prerequisite 不一致

- `dome`
  - `knowledge.py`: `proc + barracks`
  - `dataset.py`: `proc + fact`
  - 这是明确错误；`knowledge` 多写了一个 barracks 前置

- `v2rl`
  - `knowledge.py`: `weap`
  - `dataset.py`: `dome + weap`

- `3tnk`
  - `knowledge.py`: `weap`
  - `dataset.py`: `fix + weap`

- `4tnk`
  - `knowledge.py`: `weap`
  - `dataset.py`: `fix + stek + weap`

- `e2 / e4 / e7 / jeep / 1tnk / 2tnk`
  - `knowledge.py` 仍保留这些非 demo roster 项
  - 但 `context` / prompt 已经把它们视为当前 demo 不可用

### C. `world_model/core.py` 与 `task_agent/context.py` 的 roster 不一致

- `world_model` 的 `_derive_buildable_units()` 基于 `dataset.py`，可能导出：
  - `apwr`, `tent`, `afld`, `atek`, `stek`, `e6`, `heli`, `mh60` 等
- `task_agent/context.py` 会再用 `_CAPABILITY_ALLOWED_BUILDABLE` 硬切成：
  - Buildings: `powr proc barr weap dome fix`
  - Infantry: `e1 e3`
  - Vehicle: `ftrk v2rl 3tnk 4tnk harv`
  - Aircraft: `mig yak`
- 结果：Capability 看到的是 `context` 再裁过的一版 roster，而不是 `world_model` 的原始 buildability

### D. `world_model/core.py` 自己还有一层“隐式规则”

- `_derive_buildable_units()` 里只要 `has_construction_yard=True` 就会：
  - `owned_buildings.add("powr")`
- 这意味着任何依赖 `powr` 的建筑都会被视为已满足 power prerequisite
- 这不是来自 `dataset` / `registry` 的真值，而是 `world_model` 内部额外假设

---

## 2) 最小应该统一哪一层

### 推荐：下一最小 slice 先统一 **capability-facing truth table**

不要直接把 `UnitRegistry` 原始 prerequisites 扩散到 `world_model` / `knowledge` / `context`。

原因：

- `UnitRegistry` 当前保留的是 raw OpenRA prerequisites
  - 例如 `anypower`、`~barracks`、`~vehicles.soviet`、`~techlevel.medium`
- 这些 token 还没有统一的“归一化解释层”
- 直接让所有调用方都去解释这套 raw syntax，会把 slice 做大

### 最小可执行选择

先把 **Capability/WorldModel 用到的 demo truth** 统一到一层，短期建议以 `dataset.py` 为基底：

- `dataset.py` 继续承担“当前 demo 可计算 prerequisite/buildability”的唯一数据层
- `world_model/core.py` 只从这一层推导 buildability
- `task_agent/context.py` 的 capability roster 也从这一层生成，不再手写第二份
- `experts/knowledge.py` 不再维护自己的 tech prerequisite 表；只保留 roles / impacts / recovery 这类软知识

### 长期方向

长期 authoritative source 仍应回到 `UnitRegistry/YAML`，但那是下一阶段：

- 先做一层 “registry raw prerequisites -> normalized demo prerequisites” 映射
- 再替换掉 `dataset.py`

这不是当前最小 slice。

---

## 3) 先改哪些文件，不要过度设计

### 第一批：必须先改

1. `openra_state/data/dataset.py`
   - 确认当前 demo 真正支持的 roster / prerequisite
   - 把 `proc / harv / ftrk / mig / yak / 4tnk` 这些关键项先对齐
   - 明确删掉或注释“当前 demo 不支持但仍暴露给 capability”的项

2. `world_model/core.py`
   - 继续让 `_derive_buildable_units()` 只读一份 truth
   - 去掉或收紧“`has_construction_yard => implicit powr`”这种额外假设
   - 不再在这里发明第二套 prerequisite 语义

3. `task_agent/context.py`
   - `_CAPABILITY_ALLOWED_BUILDABLE` 不要继续手写第二份 roster
   - 改成从统一 truth 层导出 capability-visible roster
   - `CN_NAME_MAP` 的展示可保留，但 roster/filter 不应自己维护

### 第二批：紧接着改

4. `experts/knowledge.py`
   - 删掉或最小化 `_TECH_PREREQUISITES`
   - Expert blocker / recommendation 需要前置时，改从统一 truth 层读
   - 这里保留：
     - roles
     - downstream impacts
     - low power / awareness / recovery package
   - 不再保留第二套 tech tree

### 暂时不要动

5. `unit_registry.py`
   - 暂时不要把它改成“大而全的 prerequisite engine”
   - 它继续承担：
     - YAML-backed ids
     - aliases
     - queue/category/faction raw metadata
   - 等后面再做 raw prerequisite 归一化层

---

## 最小 slice 的一句话定义

**下一最小 slice 不是“把所有真值统一到 UnitRegistry”**，而是：

**先把 Capability / WorldModel / Expert blocker 使用的 demo prerequisite & roster 收到一份统一的、可计算的 truth table；短期用 `dataset.py` 承担这层，禁止 `knowledge.py` 和 `context.py` 再各写一份。**

这样改动最小，也最符合“先收真值，再谈大一统”的顺序。
