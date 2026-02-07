# NLU Pipeline (Product-Oriented)

This directory contains a production-style NLU pipeline for THE-Seed OpenRA:
- multi-source data collection (logs / web snippets / synthetic generation)
- LLM pre-labeling with strict JSON schema + fallback weak labeling
- manual gold labeling integration
- dataset build and split
- intent model training and evaluation
- formal smoke test with release gate checks

## Quick Start

```bash
cd nlu_pipeline
python3 scripts/run_smoke.py
```

## Phase 2 Runtime
- Runtime gateway config: `configs/runtime_gateway.yaml`
- Runtime model artifact: `artifacts/intent_model_runtime.json`
- Integrated gateway entry: `agents/nlu_gateway.py`

When `main.py` runs, commands are processed by Phase 2 gateway first:
- safe intents with sufficient confidence -> direct routed execution
- high-risk or low-confidence intents -> fallback to LLM executor

## Key Outputs
- `data/raw/*`: source corpora
- `data/labeled/prelabels.jsonl`: silver labels
- `data/manual/manual_gold_seed.jsonl`: manual gold labels
- `data/datasets/{train,dev,test}.jsonl`: training splits
- `models/intent_model.pkl`: trained intent model
- `artifacts/intent_model_runtime.json`: portable runtime model artifact
- `reports/smoke_report.md`: smoke report
- `reports/eval_metrics.json`: evaluation metrics

## Notes
- The pipeline is designed to run even without external ML packages.
- If OpenAI-compatible credentials are available, LLM pre-labeling is attempted first.
- If LLM labeling fails, the weak labeler fallback is used to keep the pipeline deterministic.
