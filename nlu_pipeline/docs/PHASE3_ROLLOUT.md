# Phase 3 Rollout (Attack Gated + Continuous Data Collection)

## Scope
- Open explicit attack intent routing under strict safety gates.
- Keep fallback as default for risky/ambiguous attack commands.
- Run continuous event collection and annotation queue generation in parallel.

## Attack Gated Rules
- Config: `nlu_pipeline/configs/runtime_gateway.yaml`
- Gate requirements:
  - intent confidence >= `attack_gated.min_confidence`
  - router intent must be `attack`
  - router score >= `attack_gated.min_router_score`
  - explicit attack verb required
  - target entity required
- If any check fails -> fallback to LLM path.

## Continuous Data Collection
- Event source: `nlu_pipeline/data/raw/logs/commands_from_logs.jsonl`
- Queue builder:
  - `python3 nlu_pipeline/scripts/build_annotation_queue_phase3.py`
- Queue output:
  - `nlu_pipeline/data/manual/annotation_queue_phase3.jsonl`
- Prioritization:
  - high-risk attack candidates
  - low-confidence predictions
  - model-router mismatch
  - router-unmatched fallbacks

## Ops Loop
1. Run smoke + queue build
   - `python3 nlu_pipeline/scripts/run_smoke.py`
   - `python3 nlu_pipeline/scripts/build_annotation_queue_phase3.py`
2. Human review queue and label
3. Rebuild dataset and retrain
4. Re-run smoke and compare metrics
