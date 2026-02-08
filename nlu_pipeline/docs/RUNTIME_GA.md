# Phase 6 GA (NLU Fully Implemented)

## Scope
- Enable production-grade NLU routing for:
  - safe intents
  - attack (strict attack gate)
  - composite sequence (strict composite gate)
- Keep unsafe composite commands (e.g. composite with attack step) on fallback path.

## Runtime Gate
- Config: `nlu_pipeline/configs/runtime_gateway.yaml`
- Current phase: `phase6_nlu_ga`
- New gate section:
  - `composite_gated.*`

## Formal Run Test
- Script: `python3 nlu_pipeline/scripts/runtime_runtest.py`
- Report:
  - `nlu_pipeline/reports/phase6_runtest_report.json`
  - `nlu_pipeline/reports/phase6_runtest_report.md`
- Gate examples:
  - route rate
  - low-risk route rate
  - attack route rate
  - composite route rate
  - unsafe composite route rate
  - route intent mismatch rate
  - latency p95

## Release Binding
- Phase5 release bundle now depends on phase6 run-test PASS.
- Release manifest includes phase6 route-rate metrics and report snapshot.

## Runtime Control Actions (enemy_control)
- `nlu_phase6_runtest`
  - trigger `runtime_runtest.py`
  - dashboard event: `nlu_job_status`
- `nlu_release_bundle`
  - trigger `release_bundle.py`
  - dashboard event: `nlu_job_status`
- `nlu_smoke`
  - trigger `run_smoke.py`
  - dashboard event: `nlu_job_status`
