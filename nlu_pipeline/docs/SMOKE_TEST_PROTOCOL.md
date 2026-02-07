# Formal Smoke Protocol

1. Data Collection
- logs extraction
- web snippet extraction
- synthetic generation
- phase4.3 batch collection (>=3000 rows)

2. Labeling
- LLM pre-label attempt
- fallback weak labeling
- manual gold override

3. Build Splits
- deterministic split by seed
- label distribution sanity checks

4. Train + Evaluate
- train intent model
- evaluate on held-out test set
- compute dangerous false positive rate

5. Gate Check
- compare metrics with `configs/gates.yaml`
- output PASS/FAIL with exact failed conditions

6. Phase4 Ops Gate
- aggregate online runtime decisions (`phase4_metrics.py`)
- execute rollback controller in dry-run mode (`phase4_auto_rollback.py --dry-run`)
- fail smoke immediately if rollback would be triggered
