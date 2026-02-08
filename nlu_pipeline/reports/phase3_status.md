# Phase 3 Status

## Result
- Phase: `phase3_attack_gated`
- Smoke: PASS
- Runtime gateway smoke: PASS

## Attack Gated Verification
- Explicit attack command `用坦克攻击敌方矿车` routed by NLU gateway.
- Non-command attack-like text `我想打个招呼` fallback to LLM.
- Negative/system commands (e.g. `停止攻击`, `打开设置`) fallback to LLM.

## Parallel Data Collection
- Event input rows: 27998
- Unique command texts: 128
- Annotation queue size: 128
- Queue file: `nlu_pipeline/data/manual/annotation_queue_phase3.jsonl`

## Notes
- Current queue size is bounded by unique texts in the current log corpus.
- To scale toward 20k+, switch from global dedup to event-level sampling and include online shadow traffic.
