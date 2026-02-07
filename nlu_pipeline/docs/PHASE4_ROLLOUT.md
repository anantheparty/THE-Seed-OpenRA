# Phase 4 Rollout (Full Release + Auto Rollback)

## Scope
- Move from attack-gated verification to production rollout controls.
- Support dynamic percentage rollout per agent.
- Add formal online health checks and auto rollback gate.

## Runtime Controls
- Config: `nlu_pipeline/configs/runtime_gateway.yaml`
- Rollout section:
  - `rollout.enabled`
  - `rollout.default_percentage`
  - `rollout.percentages_by_agent`
  - `rollout.bucket_key` (`agent|command|agent_command|identity|identity_command`)

## Live Operations (WebSocket enemy_control actions)
- `nlu_set_rollout`
  - params:
    - `percentage` (0-100)
    - optional `agent` (`human` / `enemy`)
    - optional `enabled` (`true`/`false`)
    - optional `bucket_key`
- `nlu_set_shadow`
  - params:
    - `shadow_mode` (`true`/`false`)
    - optional `enabled`
- `nlu_emergency_rollback`
  - immediate disable + rollout to 0
- `nlu_reload` / `nlu_status`

## Online Metrics
- Script: `python3 nlu_pipeline/scripts/phase4_metrics.py`
- Output:
  - `nlu_pipeline/reports/phase4_metrics.json`
  - `nlu_pipeline/reports/phase4_metrics.md`
- Core metrics:
  - `route_rate`
  - `unknown_route_reason_rate`
  - `suspicious_attack_route_rate`
  - `router_unmatched_fallback_rate`
  - `latency_ms.p95`

## Auto Rollback
- Guardrails: `nlu_pipeline/configs/phase4_guardrails.yaml`
- Controller:
  - dry-run gate check:
    - `python3 nlu_pipeline/scripts/phase4_auto_rollback.py --dry-run`
  - apply rollback:
    - `python3 nlu_pipeline/scripts/phase4_auto_rollback.py`
- Rollback report:
  - `nlu_pipeline/reports/phase4_rollback_report.json`
  - `nlu_pipeline/reports/phase4_rollback_report.md`

## Smoke Entry
- Full formal smoke:
  - `python3 nlu_pipeline/scripts/run_smoke.py`
- Includes:
  - runtime gateway smoke
  - phase3 annotation queue build
  - phase4 online metrics
  - phase4 rollback dry-run gate
