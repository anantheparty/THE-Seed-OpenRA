from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np


@dataclass
class PortableIntentPrediction:
    intent: str
    confidence: float


class PortableIntentModel:
    """Portable char ngram NB runtime model loaded from JSON artifact."""

    def __init__(
        self,
        *,
        ngram_min: int,
        ngram_max: int,
        labels: List[str],
        class_log_prior: Dict[str, float],
        token_log_prob: Dict[str, Dict[str, float]],
        unk_log_prob: Dict[str, float],
    ) -> None:
        self.ngram_min = ngram_min
        self.ngram_max = ngram_max
        self.labels = labels
        self.class_log_prior = class_log_prior
        self.token_log_prob = token_log_prob
        self.unk_log_prob = unk_log_prob

    @classmethod
    def load(cls, path: Path) -> "PortableIntentModel":
        payload = json.loads(path.read_text(encoding="utf-8"))
        model = payload.get("model", {})

        return cls(
            ngram_min=int(model.get("ngram_min", 1)),
            ngram_max=int(model.get("ngram_max", 3)),
            labels=[str(x) for x in model.get("labels", [])],
            class_log_prior={
                str(k): float(v) for k, v in dict(model.get("class_log_prior", {})).items()
            },
            token_log_prob={
                str(label): {str(tok): float(val) for tok, val in dict(tok_map).items()}
                for label, tok_map in dict(model.get("token_log_prob", {})).items()
            },
            unk_log_prob={
                str(k): float(v) for k, v in dict(model.get("unk_log_prob", {})).items()
            },
        )

    def _ngrams(self, text: str) -> List[str]:
        text = "".join((text or "").strip().lower().split())
        grams: List[str] = []
        for n in range(self.ngram_min, self.ngram_max + 1):
            if len(text) < n:
                continue
            grams.extend(text[i : i + n] for i in range(len(text) - n + 1))
        return grams or [text]

    def predict_proba(self, texts: List[str]) -> np.ndarray:
        rows = []
        for text in texts:
            grams = self._ngrams(text)
            scores = []
            for label in self.labels:
                s = float(self.class_log_prior.get(label, -100.0))
                tok_prob = self.token_log_prob.get(label, {})
                unk = float(self.unk_log_prob.get(label, -30.0))
                for g in grams:
                    s += float(tok_prob.get(g, unk))
                scores.append(s)
            m = max(scores)
            exps = [math.exp(v - m) for v in scores]
            z = sum(exps)
            probs = [v / z for v in exps]
            rows.append(probs)
        return np.array(rows)

    def predict_one(self, text: str) -> PortableIntentPrediction:
        probs = self.predict_proba([text])[0]
        idx = int(np.argmax(probs))
        return PortableIntentPrediction(intent=self.labels[idx], confidence=float(probs[idx]))
