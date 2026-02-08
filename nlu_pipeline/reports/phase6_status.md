# Phase 6 Status

## Result
- Phase: `phase6_nlu_ga`
- Smoke: PASS
- Runtime gateway smoke: PASS
- Phase6 run-test: PASS
- Phase5 release gate: PASS

## Runtime Gate Behavior
- attack intent routed only by attack-gated policy
- composite sequence routed only by composite-gated policy
- unsafe composite with attack step remains fallback

## Latest Validation Snapshot
- `intent_macro_f1`: 0.9367
- `dangerous_fp_rate`: 0.0000
- `phase6_route_rate`: 0.4486
- `phase4_latency_p95_ms`: 4.64
- `release_id`: `phase5_20260207_173720_7d6ee6b030f0`
