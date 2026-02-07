from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import Any, Dict, List

from common import load_yaml, read_jsonl
import intent_models  # noqa: F401  # ensure classes are importable during unpickle
from intent_models import CharNgramNB
from metrics import classification_metrics, confusion, label_counts
from rule_weak_labeler import WeakLabeler


def load_pickle_model(path: Path) -> Any:
    with path.open("rb") as f:
        payload = pickle.load(f)
    return payload["backend"], payload["model"]


def load_runtime_model(path: Path) -> tuple[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    model_payload = payload.get("model", {})
    model = CharNgramNB.from_dict(model_payload)
    return "char_ngram_nb_runtime", model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="nlu_pipeline/models/intent_model.pkl")
    parser.add_argument(
        "--runtime-model",
        default="nlu_pipeline/artifacts/intent_model_runtime.json",
    )
    parser.add_argument("--test", default="nlu_pipeline/data/datasets/test.jsonl")
    parser.add_argument("--out", default="nlu_pipeline/reports/eval_metrics.json")
    parser.add_argument("--out-md", default="nlu_pipeline/reports/eval_report.md")
    args = parser.parse_args()

    schema = load_yaml(Path("nlu_pipeline/configs/label_schema.yaml"))
    high_risk = set(schema.get("high_risk_intents", ["attack"]))

    runtime_model_path = Path(args.runtime_model)
    if runtime_model_path.exists():
        backend, model = load_runtime_model(runtime_model_path)
    else:
        backend, model = load_pickle_model(Path(args.model))
    test_rows = read_jsonl(Path(args.test))
    texts = [str(r.get("text", "")) for r in test_rows]
    y_true = [str(r.get("intent", "fallback_other")) for r in test_rows]
    y_pred = list(model.predict(texts))

    cls = classification_metrics(y_true, y_pred)
    conf = confusion(y_true, y_pred)

    # Dangerous FP: fallback_other samples predicted as high-risk actions
    danger_fp = 0
    fallback_total = 0
    for t, p in zip(y_true, y_pred):
        if t == "fallback_other":
            fallback_total += 1
            if p in high_risk:
                danger_fp += 1
    dangerous_fp_rate = danger_fp / fallback_total if fallback_total else 0.0

    # Slot baseline evaluation via weak labeler
    weak = WeakLabeler()
    slot_total = 0
    slot_hit = 0
    for row in test_rows:
        truth_slots = row.get("slots") if isinstance(row.get("slots"), dict) else {}
        if not truth_slots:
            continue
        pred_slots = weak.infer(str(row.get("text", ""))).get("slots", {})
        for k, v in truth_slots.items():
            slot_total += 1
            if pred_slots.get(k) == v:
                slot_hit += 1
    slot_acc = slot_hit / slot_total if slot_total else 1.0

    metrics = {
        "backend": backend,
        "test_size": len(test_rows),
        "intent_accuracy": cls["accuracy"],
        "intent_macro_f1": cls["macro_f1"],
        "dangerous_fp_rate": dangerous_fp_rate,
        "dangerous_fp_count": danger_fp,
        "fallback_total": fallback_total,
        "slot_key_accuracy": slot_acc,
        "slot_key_total": slot_total,
        "slot_key_hit": slot_hit,
        "label_distribution": label_counts(y_true),
        "per_label": cls["per_label"],
        "confusion": conf,
    }

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Evaluation Report",
        "",
        f"- backend: `{backend}`",
        f"- test_size: `{len(test_rows)}`",
        f"- intent_accuracy: `{metrics['intent_accuracy']:.4f}`",
        f"- intent_macro_f1: `{metrics['intent_macro_f1']:.4f}`",
        f"- dangerous_fp_rate: `{metrics['dangerous_fp_rate']:.4f}` ({danger_fp}/{fallback_total})",
        f"- slot_key_accuracy: `{metrics['slot_key_accuracy']:.4f}` ({slot_hit}/{slot_total})",
        "",
        "## Label Distribution",
    ]
    for k, v in sorted(metrics["label_distribution"].items()):
        md_lines.append(f"- {k}: {v}")

    Path(args.out_md).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(
        f"[evaluate] test={len(test_rows)} macro_f1={metrics['intent_macro_f1']:.4f} "
        f"dangerous_fp_rate={metrics['dangerous_fp_rate']:.4f}"
    )


if __name__ == "__main__":
    main()
