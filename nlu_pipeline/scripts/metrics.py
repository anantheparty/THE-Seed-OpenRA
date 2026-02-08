from __future__ import annotations

from collections import Counter
from typing import Dict, Iterable, List


def confusion(y_true: Iterable[str], y_pred: Iterable[str]) -> Dict[str, Dict[str, int]]:
    labels = sorted(set(y_true) | set(y_pred))
    mat = {a: {b: 0 for b in labels} for a in labels}
    for t, p in zip(y_true, y_pred):
        mat[t][p] += 1
    return mat


def classification_metrics(y_true: List[str], y_pred: List[str]) -> Dict[str, float]:
    labels = sorted(set(y_true) | set(y_pred))
    per = {}
    f1s = []
    support_total = len(y_true)

    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        support = sum(1 for t in y_true if t == label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        }
        f1s.append(f1)

    acc = sum(1 for t, p in zip(y_true, y_pred) if t == p) / support_total if support_total else 0.0
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0

    return {
        "accuracy": acc,
        "macro_f1": macro_f1,
        "per_label": per,
    }


def label_counts(y: Iterable[str]) -> Dict[str, int]:
    return dict(Counter(y))
