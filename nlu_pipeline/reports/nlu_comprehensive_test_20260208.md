# NLU Comprehensive Test Report (2026-02-08)

## 1) Test Scope
- Offline Phase6 full test set: `506` utterances (`nlu_pipeline/data/datasets/test.jsonl`)
- Offline noisy/realistic batch: `1200` utterances (`nlu_pipeline/data/raw/phase4/commands_phase43_batch.jsonl`, first 1200)
- Total utterances evaluated this round: `1706` (>100)
- Online runtime log snapshot: `5000` events (`nlu_pipeline/data/raw/online/nlu_decisions.jsonl`)

## 2) Core Metrics
- Phase6 full test set (506):
  - route_rate: `0.4486`
  - low_risk_route_rate: `0.5831`
  - attack_route_rate: `0.3028`
  - composite_route_rate: `0.4839`
  - route_intent_mismatch_rate: `0.0000`
- Noisy batch (1200):
  - route_rate: `0.5508`
  - low_risk_route_rate: `0.6351`
  - attack_route_rate: `0.3484`
  - composite_route_rate: `0.6538`
  - route_intent_mismatch_rate: `0.0000`
- Intent/slot model quality (506):
  - intent_accuracy: `0.9486`
  - intent_macro_f1: `0.9367`
  - dangerous_fp_rate: `0.0000` (count=0)
  - slot_key_accuracy: `1.0000`
- Online runtime snapshot (5000 decisions):
  - route_rate: `0.4412`
  - attack_route_rate: `0.4528`
  - p95_latency_ms: `4.79`

## 3) Main Failure Buckets (from 1200 noisy samples)
- `router_unmatched:sequence_clause_failed_2:no_intent`: 377 (31.42% of all utterances)
- `attack_router_unmatched:sequence_clause_failed_2:no_intent`: 62 (5.17% of all utterances)
- `attack_verb_missing`: 41 (3.42% of all utterances)
- `intent_router_mismatch`: 12 (1.00% of all utterances)
- `blocked_by_safety_pattern`: 12 (1.00% of all utterances)
- `composite_attack_step_forbidden`: 8 (0.67% of all utterances)
- `composite_router_intent_not_composite`: 7 (0.58% of all utterances)
- `composite_router_unmatched:sequence_clause_failed_2:low_confidence`: 7 (0.58% of all utterances)
- `composite_router_unmatched:sequence_clause_failed_3:no_intent`: 6 (0.50% of all utterances)
- `low_confidence`: 3 (0.25% of all utterances)

## 4) Representative Misses
- [produce] -> fallback (`low_confidence`): 建造雷达
- [produce] -> fallback (`intent_router_mismatch`): 建造车间
- [attack] -> fallback (`composite_router_intent_not_composite`): 派所有重坦进攻敌方基地
- [attack] -> fallback (`blocked_by_safety_pattern`): 停止攻击
- [composite_sequence] -> fallback (`composite_attack_step_forbidden`): 进攻，全面进攻
- [composite_sequence] -> fallback (`router_intent_not_safe`): 造20火箭兵，5猛犸
- [produce] -> fallback (`composite_router_intent_not_composite`): 造两个步兵
- [composite_sequence] -> fallback (`composite_attack_step_forbidden`): 先造两个步兵然后进攻敌方矿车
- [composite_sequence] -> fallback (`composite_attack_step_forbidden`): 先补3辆坦克然后然后进攻敌方矿车。
- [produce] -> fallback (`intent_router_mismatch`): 列出单位自己V2火箭车！
- [produce] -> fallback (`intent_router_mismatch`): 请列出单位我方雷达站
- [attack] -> fallback (`blocked_by_safety_pattern`): 请别攻击了先停一下！

## 5) Diagnosis
- Router+intent core能力是够用的（macro F1 0.9367，dangerous FP=0），主要损失不在分类器，而在“路由/门控规则”。
- 当前最大损失来自句尾礼貌词、语气词、否定控制词触发的分句失败/安全拦截（`sequence_clause_failed_*`、`blocked_by_safety_pattern`）。
- 攻击路径中存在较高回退（attack route约0.30~0.35），包含：攻击词表缺口、复合句被当作非复合、以及安全策略对“停止攻击”类控制语句处理不当。
- 复合意图整体可用，但“包含attack子步骤”的复合句按策略被禁止直路由（`composite_attack_step_forbidden`），这属于设计策略而非模型能力不足。

## 6) Product-Level Next Adjustments (Priority)
- P0: 语句标准化前处理
  - 在路由前新增轻量规范化器：剥离句尾礼貌词/语气词（谢谢、快点、一下、吧、哈等），保留语义核心再分句。
  - 增加否定与停火指令专门意图（如`stop_attack`），避免误入`blocked_by_safety_pattern`。
- P1: 攻击与复合路由规则增强
  - 扩展攻击动词及同义短语词典；补齐“派...进攻...”等结构。
  - 对“单句攻击但被误判为复合/非复合”的句式加规则兜底。
- P1: 生产意图歧义消解
  - 加入生产类实体白名单+约束（建筑/单位词典），降低`intent_router_mismatch`与低置信回退。
- P2: 评测集扩容与分层门禁
  - 现有506金标可继续扩至2k+，并单独建立“口语噪声集/否定控制集/复合攻击集”。
  - 门禁拆分：模型层指标（F1）与路由层指标（route_rate、fallback原因占比）分别验收。

## 7) Pass/No-Pass Recommendation
- 结论：可继续上线推进，但应优先做P0/P1规则修复后再进行下一轮GA验收。
- 触发条件建议：
  - `sequence_clause_failed_2:no_intent` 占比 < 10%
  - attack_route_rate >= 0.45（在同分布测试集）
  - blocked_by_safety_pattern 中“合法控制语句”误杀率 < 2%
