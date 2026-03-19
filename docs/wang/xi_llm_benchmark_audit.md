# xi Phase 7.2 审计（`14c0a5c`）

## 结论

这轮我**不清成 `zero-gap`**。

报告和脚本本身方向是对的，也确实能在无 key 基线模式下独立跑完；但目前还存在 **3 个 blocker**，主要集中在：

- benchmark 结果不可复现/不可保全
- T6 方法学与报告结论不一致
- 成本与优化建议没有真实测量支撑

另有 **2 个 should-fix**。

## Findings

### 1. Blocker — benchmark 每次运行都会覆盖唯一报告文件，历史实测数据不可保全，导致 Qwen 数值无法稳定复核

- [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L294) 到 [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L299)
- [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L23)

根因：脚本只把最终 markdown 写回固定路径 `docs/llm_benchmark_report.md`，没有导出原始 JSON 结果、raw response、运行环境信息或时间戳版本化产物。

后果：只要再跑一次脚本，先前的 Qwen 实测数字就会被覆盖。这个问题我本地已经复现：`QWEN_API_KEY='' python3 tests/test_llm_benchmark.py` 会把原本带 Qwen 数值的报告直接改写成 `No API key` 版本。也就是说，当前仓库里的结论不是“可复核实验结果”，而是“最近一次运行留下的快照”。

这直接卡住了 Wang 要求的两项：
- “测试方法是否可复现”
- “数据是否合理”

因为当前没有足够证据链让别人从 repo 内稳定追溯到那组 4090ms / 3051ms / 7958ms 数据。

### 2. Blocker — T6 只测了单轮 LLM 输出，却在报告里被上升成“复杂 multi-Job 协调质量好”的依据

- [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L139) 到 [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L151)
- [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L174) 到 [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L179)
- [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L48)
- [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L63) 到 [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L67)

根因：脚本对 T6 只发起一次 `provider.chat(...)`。如果模型只返回 `query_world`，评估函数就给出 `OK: querying world first (multi-turn expected)`。这最多只能说明“第一步没有完全跑偏”，不能证明它在真实 agent loop 里会完成后续 `start_job(CombatExpert, surround)`，更不能证明 multi-Job coordination 已被验证。

但报告写法已经把这条证据上升成：
- “Called `query_world` first ... this is correct multi-turn behavior”
- 以及推荐结论里的 “Correct tool_use for all tested scenarios”

这在方法学上是 overclaim。T6 当前只能支撑“复杂场景首轮响应尚可”，不能支撑“复杂协调能力已验证”。

### 3. Blocker — 成本、prompt caching、context compression、streaming 这些推荐项都不是脚本真实测出来的结果

- [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L51) 到 [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L59)
- [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L69) 到 [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L72)
- [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L87) 到 [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L123)
- [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L200) 到 [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L248)

根因：脚本只收集 4 类直接观测值：
- 单次 latency
- prompt tokens
- completion tokens
- 一个很粗的 quality note

它没有：
- 任何 cost 计算逻辑
- 任何 prompt caching on/off 对照
- 任何 context compression 变体
- 任何 streaming 测试
- 任何多次重复采样 / 方差统计

因此报告里的这些段落目前不是 benchmark 结果，而是作者推断。可以写成“hypothesis / next step”，但不能和实测结论混在一起支撑模型推荐。

## Should-fix

### 4. Should-fix — 脚本不是一个真正可参数化的 standalone benchmark 工具，`--help` 不会退出而是进入执行路径

- [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L253) 到 [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L306)

我本地验证了：`python3 tests/test_llm_benchmark.py --help` 5 秒内不会返回帮助文本，而是进入正常执行路径并卡在运行中。

这不影响“无参数可以独立跑”，但说明它还不是一个像样的 benchmark CLI。至少应该有：
- `--help`
- `--model`
- `--scenario`
- `--output`
- `--skip-live`

### 5. Should-fix — `dotenv` 是隐式依赖，脚本没有任何依赖说明或缺失提示

- [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L23) 到 [tests/test_llm_benchmark.py](/Users/kamico/work/theseed/THE-Seed-OpenRA/tests/test_llm_benchmark.py#L24)

当前环境里 `python-dotenv` 是装着的，所以脚本能跑；但如果换环境，没有任何显式依赖说明或友好错误提示。这会削弱复现性。

## Nit

### 6. Nit — 报告表格里有一处 markdown 文案残缺

- [docs/llm_benchmark_report.md](/Users/kamico/work/theseed/THE-Seed-OpenRA/docs/llm_benchmark_report.md#L29)

`GOOD: substantive answer (403 chars, data_ref=True |` 缺了右括号，属于小问题，但会影响阅读可信度。

## 本地验证

我执行了：

- `python3 -m py_compile tests/test_llm_benchmark.py`
- `QWEN_API_KEY='' python3 tests/test_llm_benchmark.py`
- `python3 tests/test_llm_benchmark.py --help`（用 5s timeout 包裹）
- 源码/报告对照：`tests/test_llm_benchmark.py` vs `docs/llm_benchmark_report.md`

结果：

- 脚本语法正确
- 在“无 key 基线模式”下可独立跑完
- `--help` 不会正常返回帮助文本
- 报告中的 T6 结论、cost/caching/streaming 建议，均强于脚本实际测量能力

## 最终判断

当前我给 Wang 的审计口径是：**不是 `zero-gap`**。

如果要让我收口这项，我建议最少补三件事：

1. benchmark 导出原始 JSON 结果和版本化报告文件，避免覆盖旧数据
2. T6 改成真实 multi-turn benchmark，或者把结论降级为“首轮响应质量”
3. 把 cost/caching/compression/streaming 改成显式“未测推断”，或补对应实验
