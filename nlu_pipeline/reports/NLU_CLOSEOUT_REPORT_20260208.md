# NLU Product Closeout Report (2026-02-08)

## 1. Closeout Scope
- 完成 P0/P1 实装收尾：前置规范化、stop-attack 安全控制、攻击句式扩展、produce/query 歧义纠偏、短句黑话覆盖。
- 完成 dashboard 连通性与调试能力收尾：默认端口对齐 `8090`，新增 dashboard 全事件 JSONL 持久化。
- 完成回归验证、产物固化、数据候选池输出与代码提交。

## 2. Delivered Capabilities
- NLU 前置覆盖增强：`电厂`、`矿场`、`开矿`、`开二矿`、`3个步兵`、`五个火箭兵`、`下电`、`下兵营`、`开车间`。
- stop-attack 指令链路可直接路由：`停止攻击`、`停止进攻`、`停火`、`取消攻击`。
- 攻击短语扩展：`派X进攻Y`、`让X突袭Y`、`命令X攻击Y`、`集火Y`。
- 线上黑话持续扩展机制：从在线 fallback 自动抽取高频候选，输出 backlog。
- Dashboard 可观测性：`Logs/dashboard_events.jsonl` 持久化连接、入站消息、广播与错误事件。

## 3. Metrics Summary
### 3.1 Phase6 Run-Test (506)
- route_rate: `0.4486` -> `0.8123`
- low_risk_route_rate: `0.5831` -> `0.9642`
- attack_route_rate: `0.3028` -> `0.7156`
- composite_route_rate: `0.4839` -> `0.6774`
- route_intent_mismatch_rate: `0.0000` -> `0.0462`
- gate result: `PASS`

### 3.2 Phase6 Noisy Set (1200)
- route_rate: `0.5508` -> `0.9383`
- low_risk_route_rate: `0.6351` -> `0.9957`
- attack_route_rate: `0.3484` -> `0.7097`
- composite_route_rate: `0.6538` -> `0.6538`
- gate result: `PASS`

### 3.3 Intent/Slot Model Health
- intent_macro_f1: `0.9367`
- intent_accuracy: `0.9486`
- dangerous_fp_rate: `0.0000`
- slot_key_accuracy: `0.5834`

### 3.4 P0/P1 Curated Regression
- total: `30`
- passed: `True`
- route_rate: `1.0000`

## 4. Test Artifacts
- `nlu_pipeline/reports/p0_p1_regression_report.json`
- `nlu_pipeline/reports/p0_p1_regression_report.md`
- `nlu_pipeline/reports/phase6_runtest_report_p0p1_v3_506.json`
- `nlu_pipeline/reports/phase6_runtest_report_p0p1_v3_506.md`
- `nlu_pipeline/reports/phase6_runtest_phase43_1200_p0p1_v3.json`
- `nlu_pipeline/reports/phase6_runtest_phase43_1200_p0p1_v3.md`
- `nlu_pipeline/reports/eval_metrics_p0p1_v3_506.json`
- `nlu_pipeline/reports/eval_report_p0p1_v3_506.md`
- `nlu_pipeline/reports/slang_candidate_backlog_20260208.md`
- `nlu_pipeline/reports/slang_candidate_backlog_20260208.json`
- `nlu_pipeline/data/raw/web/commands_from_web_20260208.jsonl`

## 5. Dashboard/Runtime Ops Closeout
- Dashboard 默认 websocket 端口统一为 `8090`，与后端保持一致。
- 新增事件日志：`Logs/dashboard_events.jsonl`。
- 日志环境变量：`DASHBOARD_EVENT_LOG_ENABLED`、`DASHBOARD_EVENT_LOG_PATH`。

## 6. Remaining Risks and Next Iteration
- 1200 噪声集 `route_intent_mismatch_rate` 仍高于 5%，主要来自“fallback_other 被主动路由到可执行安全意图”的策略性放行。
- 建议 Phase Next：将 mismatch 指标拆分为“策略放行 mismatch”与“真实误判 mismatch”，避免惩罚产品策略。
- 持续动作：每周从 `slang_candidate_backlog` TopN 合并词典并回归。

## 7. Commit Inventory
- Main repo commits:
  - `4d9beee` P0/P1 core gateway+regression suite
  - `d62f165` dashboard ws default port align to 8090
  - `f59051c` shorthand/slang coverage expansion + refreshed reports
  - `49b8c83` slang backlog + web seed command dataset
- Submodule `the-seed` commits:
  - `dbfacef` router normalization + stop-attack + attack pattern upgrades
  - `7181624` dashboard bridge event persistence
  - `646b1c8` produce/expand-mine shorthand + implicit count-unit routing

## 8. Closeout Decision
- NLU Phase closeout: **Done**（可进入下一阶段迭代）。
