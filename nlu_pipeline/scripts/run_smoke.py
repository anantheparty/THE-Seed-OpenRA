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
    run([sys.executable, "nlu_pipeline/scripts/collect_hf_dialogue_corpus.py"])
    run([sys.executable, "nlu_pipeline/scripts/generate_synthetic.py"])
    run([sys.executable, "nlu_pipeline/scripts/collect_online_batch.py"])
    run([sys.executable, "nlu_pipeline/scripts/collect_dashboard_interactions.py"])
    run([sys.executable, "nlu_pipeline/scripts/build_unlabeled_pool.py"])
    run([sys.executable, "nlu_pipeline/scripts/prelabel_llm.py", "--max-llm-calls", str(max_llm_calls)])
    run([sys.executable, "nlu_pipeline/scripts/build_dataset.py"])
    run([sys.executable, "nlu_pipeline/scripts/train_intent.py"])
    run([sys.executable, "nlu_pipeline/scripts/evaluate.py"])
    run([sys.executable, "nlu_pipeline/scripts/smoke_runtime_gateway.py"])
    run([sys.executable, "nlu_pipeline/scripts/runtime_runtest.py"])
    run([sys.executable, "nlu_pipeline/scripts/build_annotation_queue.py"])
    run([sys.executable, "nlu_pipeline/scripts/runtime_metrics.py"])
    run([sys.executable, "nlu_pipeline/scripts/runtime_auto_rollback.py", "--dry-run"])
    run([sys.executable, "nlu_pipeline/scripts/release_bundle.py"])

    gates = load_yaml(PROJECT_ROOT / "nlu_pipeline/configs/gates.yaml").get("smoke", {})
    dataset_report = json.loads((PROJECT_ROOT / "nlu_pipeline/reports/dataset_report.json").read_text(encoding="utf-8"))
    prelabel_report = json.loads((PROJECT_ROOT / "nlu_pipeline/reports/prelabel_report.json").read_text(encoding="utf-8"))
    eval_metrics = json.loads((PROJECT_ROOT / "nlu_pipeline/reports/eval_metrics.json").read_text(encoding="utf-8"))
    runtime_gateway_smoke = json.loads(
        (PROJECT_ROOT / "nlu_pipeline/reports/runtime_gateway_smoke.json").read_text(encoding="utf-8")
    )
    runtime_runtest = json.loads(
        (PROJECT_ROOT / "nlu_pipeline/reports/phase6_runtest_report.json").read_text(encoding="utf-8")
    )
    annotation_queue_report = json.loads(
        (PROJECT_ROOT / "nlu_pipeline/reports/annotation_queue_phase3_report.json").read_text(encoding="utf-8")
    )
    phase4_metrics = json.loads((PROJECT_ROOT / "nlu_pipeline/reports/phase4_metrics.json").read_text(encoding="utf-8"))
    phase4_rollback_report = json.loads(
        (PROJECT_ROOT / "nlu_pipeline/reports/phase4_rollback_report.json").read_text(encoding="utf-8")
    )
    phase5_release_report = json.loads(
        (PROJECT_ROOT / "nlu_pipeline/reports/phase5_release_report.json").read_text(encoding="utf-8")
    )

    min_macro_f1 = float(gates.get("min_macro_f1", 0.0))
    max_dangerous_fp_rate = float(gates.get("max_dangerous_fp_rate", 1.0))
    min_samples_test = int(gates.get("min_samples_test", 1))
    required_intents = set(gates.get("require_intents_present", []))
    runtime_gate = gates.get("runtime", gates.get("phase4", {}))
    min_runtime_events = int(runtime_gate.get("min_events", 1))
    min_runtime_route_rate = float(runtime_gate.get("min_route_rate", 0.0))
    max_runtime_unknown_route_reason_rate = float(runtime_gate.get("max_unknown_route_reason_rate", 1.0))

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

    if not (PROJECT_ROOT / "nlu_pipeline/artifacts/intent_model_runtime.json").exists():
        failures.append("missing runtime model artifact: nlu_pipeline/artifacts/intent_model_runtime.json")

    if not bool(runtime_gateway_smoke.get("passed", False)):
        failures.append("runtime gateway smoke failed")
    if not bool(runtime_runtest.get("passed", False)):
        failures.append("runtime run-test failed")
    queue_size = int(annotation_queue_report.get("queue_size", 0))
    unique_texts = int(annotation_queue_report.get("unique_texts", 0))
    min_queue_required = min(200, unique_texts)
    if queue_size < min_queue_required:
        failures.append(
            f"annotation queue too small ({queue_size} < required {min_queue_required})"
        )

    dist_keys = set(dataset_report.get("distribution", {}).keys())
    missing_intents = sorted(required_intents - dist_keys)
    if missing_intents:
        failures.append(f"required intents missing in dataset: {', '.join(missing_intents)}")

    phase4_totals = phase4_metrics.get("totals", {})
    phase4_events = int(phase4_totals.get("events", 0))
    phase4_route_rate = float(phase4_totals.get("route_rate", 0.0))
    phase4_unknown_route_reason_rate = float(phase4_totals.get("unknown_route_reason_rate", 0.0))
    if phase4_events < min_runtime_events:
        failures.append(f"runtime events too small ({phase4_events} < {min_runtime_events})")
    if phase4_route_rate < min_runtime_route_rate:
        failures.append(
            f"runtime route_rate {phase4_route_rate:.4f} < min_runtime_route_rate {min_runtime_route_rate:.4f}"
        )
    if phase4_unknown_route_reason_rate > max_runtime_unknown_route_reason_rate:
        failures.append(
            "runtime unknown_route_reason_rate "
            f"{phase4_unknown_route_reason_rate:.4f} > max {max_runtime_unknown_route_reason_rate:.4f}"
        )
    if bool(phase4_rollback_report.get("triggered", False)):
        failures.append("runtime auto rollback dry-run triggered")
    if not bool(phase5_release_report.get("passed", False)):
        failures.append("release bundle gate failed")

    passed = len(failures) == 0

    summary = {
        "passed": passed,
        "failures": failures,
        "metrics": eval_metrics,
        "dataset": dataset_report,
        "prelabel": prelabel_report,
        "runtime_gateway_smoke": runtime_gateway_smoke,
        "runtime_runtest": runtime_runtest,
        "annotation_queue": annotation_queue_report,
        "phase4_metrics": phase4_metrics,
        "phase4_rollback": phase4_rollback_report,
        "phase5_release": phase5_release_report,
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
        f"- runtime_gateway_passed: {runtime_gateway_smoke.get('passed')}",
        f"- runtime_gateway_route_count: {runtime_gateway_smoke.get('route_count')}",
        f"- runtime_runtest_passed: {runtime_runtest.get('passed')}",
        f"- runtime_runtest_route_rate: {float(runtime_runtest.get('totals', {}).get('route_rate', 0.0)):.4f}",
        f"- annotation_queue_size: {annotation_queue_report.get('queue_size')}",
        f"- phase4_events: {phase4_events}",
        f"- phase4_route_rate: {phase4_route_rate:.4f}",
        f"- phase4_unknown_route_reason_rate: {phase4_unknown_route_reason_rate:.4f}",
        f"- phase4_rollback_triggered: {phase4_rollback_report.get('triggered')}",
        f"- phase5_release_passed: {phase5_release_report.get('passed')}",
        f"- phase5_release_id: {phase5_release_report.get('release_id')}",
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
