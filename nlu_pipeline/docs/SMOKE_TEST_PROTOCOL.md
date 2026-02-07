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

7. Phase5 Release Gate
- build release bundle (`phase5_release_bundle.py`)
- freeze model/data metadata into release manifest + model card + data card
- fail smoke if release gates do not pass

8. Phase6 Runtime GA Test
- run formal runtime replay test (`phase6_run_test.py`)
- verify composite gated route and unsafe composite fallback behavior
- fail smoke if phase6 run-test gates do not pass
