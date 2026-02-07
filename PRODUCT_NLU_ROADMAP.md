# THE-Seed OpenRA 产品级 NLU Roadmap

## 1. 目标与范围
- 目标: 构建可上线的低时延 NLU 层, 在不牺牲安全性的前提下替代大量简单指令的 LLM 路径。
- 适用范围: OpenRA 对局内中文自然语言指令。
- 非目标: 不在该阶段解决开放域对话; 不在该阶段替代复杂策略规划。

## 2. 产品级成功标准 (Release Gates)
- Gate A: 安全性
  - 危险误触发率 (非军事指令触发攻击/生产等动作) <= 0.1%
  - 高风险意图 (attack/dispatch_attack) 在低置信度场景下 100% 触发回退/拦截
- Gate B: 准确性
  - 意图识别 Macro-F1 >= 0.96
  - 核心槽位 F1 >= 0.95 (unit/count/faction/range/actor_id/group_id)
  - 复合指令步骤边界准确率 >= 0.95
- Gate C: 性能
  - P95 路由耗时 <= 25ms (CPU, 单指令)
  - 简单指令 LLM 回退率 <= 15%
- Gate D: 业务收益
  - 指令端到端 P95 时延较当前主流程下降 >= 40%
  - 用户可感知误操作投诉降低 >= 80%

## 3. 系统架构 (产品化版本)
- NLU Gateway (新增, 先旁路后主路)
  - 输入: 用户文本 + 会话上下文 + 游戏快照摘要
  - 输出: `NLUDecision` (intent, slots, confidence, risk_level, trace_id)
- Intent Classifier
  - 候选技术: 字符 n-gram + LinearSVM/LogReg; 备选轻量蒸馏模型
  - 输出 top-k intent + calibrated confidence
- Slot Extractor
  - 方案: 规则 + CRF/轻量序列标注混合
  - 槽位: unit, count, faction, range, actor_id, group_id, attacker_type, target_type
- Policy & Guardrail
  - 风险策略: 高风险意图需更高阈值; 否则强制回退 LLM
  - 安全词典: 否定词/系统词/聊天词优先拦截
- Executor Router
  - `HIGH_CONFIDENCE`: 结构化动作模板执行
  - `LOW_CONFIDENCE`: 回退 `SimpleExecutor(CodeGenNode)`
- Observability
  - 全链路埋点: route_source, confidence, fallback_reason, execution_result
  - 离线回放与在线抽样复盘

## 4. 数据集建设

### 4.1 标注体系
- 意图标签集 (首版)
  - deploy_mcv, produce, mine, explore, attack, query_actor, composite_sequence, fallback_other
- 槽位标签集
  - unit, count, faction, range, actor_id, group_id, attacker_type, target_type
- 元标签
  - `risk_level` (low/medium/high), `map_context_required` (bool)

### 4.2 数据来源
- 线上真实指令日志 (主来源)
  - 去标识化后进入标注池
- 离线脚本扩增 (次来源)
  - 同义改写、口语化改写、错别字注入
- 负样本专库 (必须)
  - 聊天语句、系统操作语句、模糊表达、否定句

### 4.3 数据规模与配比 (首发建议)
- 总样本 >= 30,000
- 负样本占比 25%~35%
- 高风险意图样本 >= 6,000
- 复合指令样本 >= 4,000
- 按版本冻结训练/验证/测试集 (建议 7:1.5:1.5)

### 4.4 标注质检
- 双人标注 + 仲裁
- 抽样复检 >= 10%
- 标注一致性 (Cohen's Kappa) >= 0.9

## 5. 模型训练与评测

### 5.1 训练管线
- 特征工程
  - 字符 n-gram, 关键词布尔特征, 句型特征, 否定词特征
- 模型训练
  - 意图: LinearSVM/LogReg (先上线), 后续可升级轻量 Transformer
  - 槽位: CRF/规则融合
- 置信度校准
  - Platt scaling / isotonic regression

### 5.2 离线评测
- 主指标
  - Intent Macro-F1, Slot F1, Composite Boundary F1
- 风险指标
  - Dangerous False Positive Rate
  - Attack Intent Precision
- 鲁棒性指标
  - 错别字集、口语集、噪声集分桶评测

### 5.3 回归基线
- 与当前 rule-based 路由并行评测
- 任何版本发布前必须满足: 全局指标不退化 + 风险指标更优

## 6. 集成与发布策略

### 6.1 接入原则
- 当前已将 rule_gen 有效代码入主分支, 但默认不生效。
- 新 NLU 产品化接入采用 feature flag:
  - `nlu.router.enabled`
  - `nlu.router.shadow_mode`
  - `nlu.router.high_risk_block`

### 6.2 发布分期
- Phase 0 (已完成)
  - 规则路由代码归档到主分支, 作为基线与弱标签器
- Phase 1: Shadow Mode
  - 在线仅预测不执行, 与 LLM 结果对比
- Phase 2: Safe Intents On
  - 仅开放 low-risk intents (deploy/mine/query/explore)
- Phase 3: Attack Gated
  - 开放 attack, 需更高阈值 + 额外保护策略
- Phase 4: Full Rollout
  - 按租户/房间灰度放量至 100%

### 6.3 回滚机制
- 一键降级到纯 LLM 路径
- 指标超阈值自动回滚
- 保留 7 天可追溯路由决策日志

## 7. 工程与质量保障
- 测试分层
  - 单元测试: intent/slot/guardrail
  - 集成测试: NLU -> Router -> Executor
  - 回放测试: 历史真实指令集
- 测试门槛
  - 高风险路径 100% 覆盖
  - 回退逻辑覆盖 >= 95%
- 代码质量
  - 训练与推理版本号绑定
  - 模型卡 + 数据卡 + 变更日志

## 8. 安全与合规
- 数据最小化与脱敏
- 日志访问控制与保留策略
- 模型发布审批 (双签)
- 高风险动作二次策略校验

## 9. 团队分工与里程碑
- M1 (第 1-2 周): 标注规范、数据管线、负样本库
- M2 (第 3-4 周): 第一版模型训练与离线评测
- M3 (第 5-6 周): Shadow Mode 上线与对照分析
- M4 (第 7-8 周): Safe Intents 放量
- M5 (第 9-10 周): Attack Gated + 全量发布评审

角色建议:
- NLP Owner: 数据与模型
- Gameplay Owner: 动作语义与安全策略
- Platform Owner: 网关、灰度、监控与回滚

## 10. 本仓库落地清单 (本次提交)
- 已摘取 `the-seed` 子模块 `origin/rule_gen` 的有效代码至 `main`。
- 代码现状: 默认未接入现有主执行链路, 仅作为后续产品化基线能力。
- 本文档作为产品级落地路线与验收门禁基准。

## 11. Phase 2 实施状态 (2026-02-07)
- 已完成:
  - NLU 训练与评估流水线骨架 (`nlu_pipeline/`)
  - 多源数据采集、LLM 预标注、人工 gold 标注、训练与评估冒烟
  - 可发布 runtime 模型产物 (`nlu_pipeline/artifacts/intent_model_runtime.json`)
  - 运行态 NLU 网关接入主链路 (`agents/nlu_gateway.py`, `main.py`)
  - Safe Intents On + High Risk Fallback 策略
  - 运行态网关冒烟 (`nlu_pipeline/scripts/smoke_runtime_gateway.py`)
- 当前策略:
  - 高风险意图 (attack/composite_sequence) 强制回退 LLM
  - 低风险意图按阈值命中后直通执行

## 12. Phase 3 实施状态 (2026-02-08)
- 已完成:
  - `Attack Gated` 落地: 攻击意图满足严格门禁时允许前置路由直通
  - 非命令/否定/系统类语句继续强制回退
  - 并行数据收集与标注队列构建 (`build_annotation_queue_phase3.py`)
  - Phase 3 冒烟通过（含运行态网关与标注队列门禁）
- 当前策略:
  - attack: 高阈值 + 显式攻击词 + 目标实体 + 路由一致性校验后才直通
  - composite_sequence: 仍默认回退（可控放量）

## 13. Phase 4 实施状态 (2026-02-08)
- 已完成:
  - 全量放量控制: `runtime_gateway.yaml` 增加按 agent 百分比灰度配置
  - 在线决策埋点增强: 增加 `latency_ms / risk_level / rollout_* / execution_success`
  - Phase4 运行指标聚合脚本: `nlu_pipeline/scripts/phase4_metrics.py`
  - 自动回滚控制器: `nlu_pipeline/scripts/phase4_auto_rollback.py`
  - 主进程运行时控制动作:
    - `nlu_set_rollout`
    - `nlu_set_shadow`
    - `nlu_emergency_rollback`
    - `nlu_status`
  - 正式冒烟流程升级: Phase4 指标与回滚 dry-run 门禁纳入 `run_smoke.py`
- 当前策略:
  - 默认全量 (`100%`) 放量，可按 agent 动态调节
  - 线上决策持续采集用于回放、复盘与再训练
  - 超阈值即触发自动回滚判定（dry-run 先行，正式可切换 apply）

## 14. Phase 4.3 数据规模推进 (2026-02-08)
- 新增:
  - Phase4.3 批量采集脚本 `nlu_pipeline/scripts/collect_phase43_batch.py`
  - 采集配置 `nlu_pipeline/configs/phase43_collection.yaml`
  - 采集批次产物 `nlu_pipeline/data/raw/phase4/commands_phase43_batch.jsonl`
  - 采集报告 `nlu_pipeline/reports/phase43_collection_report.json`
- 本轮结果:
  - 新增采集样本 `3200` 条（满足“至少 3000 条”目标）
  - 原始事件总量提升至 `31429` 条（logs/web/synth/online/phase4 合计）
  - 可训练数据集总量提升至 `3259` 条（train/dev/test = 2278/485/496）
- 质量控制:
  - 模板样本优先采用 `intent_hint` 作为标签真值，避免弱标注噪声污染
  - 正式冒烟 `run_smoke.py` 已接入 phase4.3 采集步骤并 PASS

## 15. Phase 5 实施状态 (2026-02-08)
- 已完成:
  - 发布工程化脚本 `nlu_pipeline/scripts/phase5_release_bundle.py`
  - 发布门禁配置 `nlu_pipeline/configs/phase5_release.yaml`
  - 发布文档 `nlu_pipeline/docs/PHASE5_RELEASE.md`
  - 正式冒烟接入 phase5 gate（release bundle + model/data card + manifest）
- 产物:
  - `nlu_pipeline/releases/<release_id>/manifest.json`
  - `nlu_pipeline/releases/<release_id>/model_card.md`
  - `nlu_pipeline/releases/<release_id>/data_card.md`
  - `nlu_pipeline/releases/<release_id>/changelog.md`
- 当前策略:
  - 训练数据与推理模型强绑定，支持发布可追溯
  - 不满足 phase5 门禁则阻断发布

## 16. Phase 6 实施状态 (2026-02-08)
- 已完成:
  - 运行态配置进入 `phase6_nlu_ga`（`runtime_gateway.yaml`）
  - `composite_sequence` 门禁直通能力（`composite_gated`）实装
  - 不安全复合指令（含攻击步骤）强制回退
  - 正式跑测脚本 `nlu_pipeline/scripts/phase6_run_test.py`
  - `run_smoke.py` 接入 phase6 跑测门禁
  - phase5 release gate 绑定 phase6 跑测通过条件
- 当前策略:
  - 安全复合指令可走 NLU 前置直通
  - 含攻击复合仍受严格限制，默认回退 LLM
 - 验收结果:
  - `run_smoke.py`: PASS
  - `phase6_run_test.py`: PASS
  - 最新 release: `phase5_20260207_173720_7d6ee6b030f0`
