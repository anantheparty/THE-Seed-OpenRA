# Phase 5 Release Engineering (Product Delivery)

## Scope
- Bind training data snapshot and runtime model into a release artifact.
- Produce product-facing release documents automatically.
- Enforce release gates in formal smoke.

## Release Bundle
- Script: `python3 nlu_pipeline/scripts/release_bundle.py`
- Config: `nlu_pipeline/configs/release_bundle.yaml`
- Output:
  - `nlu_pipeline/releases/<release_id>/manifest.json`
  - `nlu_pipeline/releases/<release_id>/model_card.md`
  - `nlu_pipeline/releases/<release_id>/data_card.md`
  - `nlu_pipeline/releases/<release_id>/changelog.md`
  - `nlu_pipeline/reports/phase5_release_report.json`
  - `nlu_pipeline/reports/phase5_release_manifest.json`

## Gate Policy
- Dataset total must satisfy product threshold.
- Intent macro-F1 and dangerous FP rate must satisfy release threshold.
- High-risk and composite sample counts are required.
- Smoke must pass and phase4 rollback must not be triggered.

## Smoke Integration
- `run_smoke.py` now executes phase5 release bundling automatically.
- Any phase5 gate breach will fail smoke and block release.
