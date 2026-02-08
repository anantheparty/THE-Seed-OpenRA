# Phase 2 Rollout (Safe Intents On)

## Scope
- Enable NLU direct execution only for safe intents:
  - deploy_mcv
  - produce
  - explore
  - mine
  - query_actor
- Force fallback for high-risk intents (e.g. attack/composite_sequence).

## Runtime Components
- Gateway: `agents/nlu_gateway.py`
- Config: `nlu_pipeline/configs/runtime_gateway.yaml`
- Runtime model artifact: `nlu_pipeline/artifacts/intent_model_runtime.json`

## Rollout Steps
1. Train and publish runtime artifact
   - `python3 nlu_pipeline/scripts/run_smoke.py`
2. Start backend
   - `python3 main.py`
3. Check startup logs for gateway status
   - `Human NLU status`
   - `Enemy NLU status`
4. Trigger hot reload (optional, from websocket control)
   - action: `nlu_reload`

## Operational Guardrails
- Keep `high_risk.force_fallback=true` in phase 2.
- Keep `shadow_mode=false` only after smoke PASS.
- Tighten confidence thresholds if dangerous FP rises.

## Rollback
- Set `enabled: false` in `runtime_gateway.yaml`
- Or remove runtime artifact file to force fallback.
