from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import List

from common import PROJECT_ROOT, load_yaml


def run(cmd: List[str]) -> None:
    print("[run]", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    smoke_report = PROJECT_ROOT / "nlu_pipeline/reports/smoke_report.md"
    smoke_json = PROJECT_ROOT / "nlu_pipeline/reports/smoke_report.json"

    cfg = load_yaml(PROJECT_ROOT / "nlu_pipeline/configs/pipeline.yaml")
    max_llm_calls = int(cfg.get("llm", {}).get("max_calls", 12))

    run([sys.executable, "nlu_pipeline/scripts/collect_logs.py"])
    run([sys.executable, "nlu_pipeline/scripts/collect_web_corpus.py"])
    run([sys.executable, "nlu_pipeline/scripts/generate_synthetic.py"])
    run([sys.executable, "nlu_pipeline/scripts/build_unlabeled_pool.py"])
    run([sys.executable, "nlu_pipeline/scripts/prelabel_llm.py", "--max-llm-calls", str(max_llm_calls)])
    run([sys.executable, "nlu_pipeline/scripts/build_dataset.py"])
    run([sys.executable, "nlu_pipeline/scripts/train_intent.py"])
    run([sys.executable, "nlu_pipeline/scripts/evaluate.py"])

    gates = load_yaml(PROJECT_ROOT / "nlu_pipeline/configs/gates.yaml").get("smoke", {})
    dataset_report = json.loads((PROJECT_ROOT / "nlu_pipeline/reports/dataset_report.json").read_text(encoding="utf-8"))
    prelabel_report = json.loads((PROJECT_ROOT / "nlu_pipeline/reports/prelabel_report.json").read_text(encoding="utf-8"))
    eval_metrics = json.loads((PROJECT_ROOT / "nlu_pipeline/reports/eval_metrics.json").read_text(encoding="utf-8"))

    min_macro_f1 = float(gates.get("min_macro_f1", 0.0))
    max_dangerous_fp_rate = float(gates.get("max_dangerous_fp_rate", 1.0))
    min_samples_test = int(gates.get("min_samples_test", 1))
    required_intents = set(gates.get("require_intents_present", []))

    failures: List[str] = []
    if eval_metrics.get("intent_macro_f1", 0.0) < min_macro_f1:
        failures.append(
            f"macro_f1 {eval_metrics.get('intent_macro_f1', 0.0):.4f} < min_macro_f1 {min_macro_f1:.4f}"
        )

    if eval_metrics.get("dangerous_fp_rate", 1.0) > max_dangerous_fp_rate:
        failures.append(
            f"dangerous_fp_rate {eval_metrics.get('dangerous_fp_rate', 1.0):.4f} > max {max_dangerous_fp_rate:.4f}"
        )

    if int(eval_metrics.get("test_size", 0)) < min_samples_test:
        failures.append(
            f"test_size {eval_metrics.get('test_size', 0)} < min_samples_test {min_samples_test}"
        )

    dist_keys = set(dataset_report.get("distribution", {}).keys())
    missing_intents = sorted(required_intents - dist_keys)
    if missing_intents:
        failures.append(f"required intents missing in dataset: {', '.join(missing_intents)}")

    passed = len(failures) == 0

    summary = {
        "passed": passed,
        "failures": failures,
        "metrics": eval_metrics,
        "dataset": dataset_report,
        "prelabel": prelabel_report,
    }
    smoke_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# NLU Smoke Report",
        "",
        f"- result: {'PASS' if passed else 'FAIL'}",
        f"- test_size: {eval_metrics.get('test_size')}",
        f"- intent_macro_f1: {eval_metrics.get('intent_macro_f1'):.4f}",
        f"- dangerous_fp_rate: {eval_metrics.get('dangerous_fp_rate'):.4f}",
        f"- slot_key_accuracy: {eval_metrics.get('slot_key_accuracy'):.4f}",
        f"- llm_enabled: {prelabel_report.get('llm_enabled')}",
        f"- llm_calls: {prelabel_report.get('llm_calls')}",
        f"- llm_success: {prelabel_report.get('llm_success')}",
        f"- fallback_count: {prelabel_report.get('fallback_count')}",
        "",
    ]
    if failures:
        lines.append("## Gate Failures")
        for f in failures:
            lines.append(f"- {f}")
    else:
        lines.append("## Gate Check")
        lines.append("- All smoke gates passed")

    smoke_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[run_smoke] result={'PASS' if passed else 'FAIL'} failures={len(failures)}")

    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
