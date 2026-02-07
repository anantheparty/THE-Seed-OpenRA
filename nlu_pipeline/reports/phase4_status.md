# Phase 4 Status

## Result
- Phase: `phase4_full_rollout`
- Smoke: PASS
- Runtime gateway smoke: PASS
- Phase4 rollback dry-run: PASS (not triggered)

## Runtime Metrics (24h window)
- Events: 14
- Route rate: 0.4286
- Fallback rate: 0.5714
- Unknown route reason rate: 0.0000
- Suspicious attack route rate: 0.0000
- Latency p95: 4.54 ms

## Rollout Control
- Runtime config supports per-agent percentage rollout.
- Live control actions enabled:
  - `nlu_set_rollout`
  - `nlu_set_shadow`
  - `nlu_emergency_rollback`
  - `nlu_status`
