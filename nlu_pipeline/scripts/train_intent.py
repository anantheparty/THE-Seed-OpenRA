from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path
from typing import List

from common import read_jsonl
from intent_models import CharNgramNB, SklearnIntentModel
from metrics import classification_metrics, label_counts


def load_split(path: Path) -> tuple[List[str], List[str]]:
    rows = read_jsonl(path)
    texts = [str(r.get("text", "")) for r in rows]
    labels = [str(r.get("intent", "fallback_other")) for r in rows]
    return texts, labels


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default="nlu_pipeline/data/datasets/train.jsonl")
    parser.add_argument("--dev", default="nlu_pipeline/data/datasets/dev.jsonl")
    parser.add_argument("--model-out", default="nlu_pipeline/models/intent_model.pkl")
    parser.add_argument("--report", default="nlu_pipeline/reports/train_report.json")
    args = parser.parse_args()

    x_train, y_train = load_split(Path(args.train))
    x_dev, y_dev = load_split(Path(args.dev))

    backend = "char_ngram_nb"
    try:
        model = SklearnIntentModel()
        backend = "sklearn_logreg"
    except Exception:
        model = CharNgramNB()

    model.fit(x_train, y_train)
    pred = model.predict(x_dev)
    metrics = classification_metrics(y_dev, pred)

    payload = {
        "backend": backend,
        "model": model,
    }
    Path(args.model_out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.model_out).open("wb") as f:
        pickle.dump(payload, f)

    report = {
        "backend": backend,
        "train_size": len(x_train),
        "dev_size": len(x_dev),
        "dev_metrics": metrics,
        "train_distribution": label_counts(y_train),
        "dev_distribution": label_counts(y_dev),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[train_intent] backend={backend} train={len(x_train)} dev={len(x_dev)} "
        f"dev_macro_f1={metrics['macro_f1']:.4f}"
    )


if __name__ == "__main__":
    main()
