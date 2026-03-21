# Performance Baseline — v1.0 (2026-03-31)

## 测试环境

- Mock GameAPI + Mock WorldModel（非 live 游戏）
- LLM：Qwen3.5 qwen-plus（真实 API）+ MockProvider（本地）
- 测试覆盖：T1-T11 全量 E2E

## 1. 运行时组件基线（Mock 环境）

来源：`docs/wang/phase7_e2e_benchmark_summary.json`

| 组件 | 指标 | Count | Avg | P95 | Max | 预算 | 状态 |
|---|---|---:|---:|---:|---:|---|---|
| job_tick | 每次 Job tick | 220 | 0.37ms | 0.80ms | 2.48ms | 100ms (10Hz) | 远低于预算 |
| world_refresh | WorldModel 刷新 | 214 | 0.17ms | 0.55ms | 0.75ms | 100ms | 远低于预算 |
| tool_exec | Tool handler 执行 | 254 | 0.03ms | 0.15ms | 0.66ms | 10ms | 远低于预算 |
| llm_call | LLM 调用(mock) | 16 | 0.12ms | 0.20ms | 0.22ms | N/A (mock) | — |
| expert_logic | Expert 评分逻辑 | 7 | 0.02ms | 0.04ms | 0.04ms | — | — |

**注意**：以上是 Mock 环境数据。真实 GameAPI（Socket RPC port 7445）会增加网络延迟。

## 2. LLM 延迟基线（真实 Qwen API）

来源：`docs/llm_benchmark_report.md`

| 场景 | 延迟 | Prompt tok | Completion tok | 预算 | 状态 |
|---|---|---:|---:|---|---|
| T1 简单意图（侦察） | 4.1s | 1538 | 122 | <5s | 合格 |
| T6 复杂意图（包围） | 3.1s | 1564 | 122 | <5s | 合格 |
| T9 查询回答 | 8.0s | 124 | 288 | <10s | 合格 |

**关键洞察**：
- system prompt 约 800+ tokens，每次调用都重发 → prompt caching 可节省约 50%
- T9 延迟高因为生成 288 tokens → streaming 可改善感知延迟
- 所有场景 LLM 延迟 >> Job tick 延迟 → LLM 是唯一瓶颈

## 3. 已识别优化目标

| # | 优化项 | 预期收益 | 状态 |
|---|---|---|---|
| O1 | Prompt caching（system prompt 复用） | LLM 延迟 -30~50% | 未测 |
| O2 | Streaming 输出 | T9 感知延迟改善 | 未测 |
| O3 | Context 压缩（简单意图少注入） | prompt tokens -20~40% | 未测 |
| O4 | tick_interval 动态调整 | 空闲时降频省 CPU | 未测 |
| O5 | WorldModel 增量刷新 | 减少 GameAPI 调用量 | 未测 |
| O6 | qwen-turbo 替代简单意图 | T1 延迟 <2s | 未测 |

## 4. 性能回归检测

运行方式：
```bash
# 运行全量 E2E + 导出 benchmark
python3 tests/test_e2e_full.py

# 查看 benchmark 汇总
python3 -c "import logging_system; print(logging_system.summarize_benchmarks())"

# 导出 JSON 对比
python3 -c "import logging_system; logging_system.export_benchmark_report_json('docs/wang/benchmark_YYYYMMDD.json')"

# LLM 实测
python3 tests/test_llm_benchmark.py --model qwen-plus
```

每次优化后跑一次，对比基线数据。

## 5. 预算定义（来自 design.md）

| 约束 | 值 | 依据 |
|---|---|---|
| GameLoop tick | 100ms (10Hz) | design.md §2 |
| CombatJob tick | 200ms | design.md §2 |
| ReconJob tick | 1000ms | design.md §2 |
| EconomyJob tick | 5000ms | design.md §2 |
| LLM 简单意图 | <5s | 可接受的游戏节奏 |
| LLM 复杂意图 | <5s | Job 自主运行，不阻塞 |
| LLM 查询 | <10s | 不阻塞游戏 |
| default_if_timeout | 3s | design.md §5（decision_request 默认等待） |
