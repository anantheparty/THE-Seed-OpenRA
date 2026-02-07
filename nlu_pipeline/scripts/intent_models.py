from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Dict, List

import numpy as np


class CharNgramNB:
    def __init__(self, ngram_min: int = 1, ngram_max: int = 3, alpha: float = 1.0) -> None:
        self.ngram_min = ngram_min
        self.ngram_max = ngram_max
        self.alpha = alpha
        self.labels: List[str] = []
        self.class_log_prior: Dict[str, float] = {}
        self.token_log_prob: Dict[str, Dict[str, float]] = {}
        self.unk_log_prob: Dict[str, float] = {}
        self.vocab: set[str] = set()

    def _ngrams(self, text: str) -> List[str]:
        text = "".join((text or "").strip().lower().split())
        grams: List[str] = []
        for n in range(self.ngram_min, self.ngram_max + 1):
            if len(text) < n:
                continue
            grams.extend(text[i : i + n] for i in range(len(text) - n + 1))
        return grams or [text]

    def fit(self, texts: List[str], labels: List[str]) -> None:
        self.labels = sorted(set(labels))
        class_counts = Counter(labels)
        total = len(labels)

        token_counts: Dict[str, Counter] = {label: Counter() for label in self.labels}
        total_tokens: Dict[str, int] = defaultdict(int)

        for text, label in zip(texts, labels):
            grams = self._ngrams(text)
            token_counts[label].update(grams)
            total_tokens[label] += len(grams)
            self.vocab.update(grams)

        vocab_size = max(1, len(self.vocab))
        for label in self.labels:
            self.class_log_prior[label] = math.log(class_counts[label] / total)
            denom = total_tokens[label] + self.alpha * vocab_size
            self.unk_log_prob[label] = math.log(self.alpha / denom)
            probs: Dict[str, float] = {}
            for tok in self.vocab:
                probs[tok] = math.log((token_counts[label][tok] + self.alpha) / denom)
            self.token_log_prob[label] = probs

    def predict_proba(self, texts: List[str]) -> np.ndarray:
        rows = []
        for text in texts:
            grams = self._ngrams(text)
            scores = []
            for label in self.labels:
                s = self.class_log_prior[label]
                tok_prob = self.token_log_prob[label]
                unk = self.unk_log_prob[label]
                for g in grams:
                    s += tok_prob.get(g, unk)
                scores.append(s)

            m = max(scores)
            exps = [math.exp(v - m) for v in scores]
            z = sum(exps)
            probs = [v / z for v in exps]
            rows.append(probs)
        return np.array(rows)

    def predict(self, texts: List[str]) -> List[str]:
        probs = self.predict_proba(texts)
        idx = np.argmax(probs, axis=1)
        return [self.labels[i] for i in idx]


class SklearnIntentModel:
    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline

        self.pipeline = Pipeline(
            [
                ("tfidf", TfidfVectorizer(analyzer="char", ngram_range=(1, 3), min_df=1)),
                ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", solver="lbfgs")),
            ]
        )

    def fit(self, texts: List[str], labels: List[str]) -> None:
        self.pipeline.fit(texts, labels)

    def predict(self, texts: List[str]) -> List[str]:
        return list(self.pipeline.predict(texts))

    def predict_proba(self, texts: List[str]) -> np.ndarray:
        if hasattr(self.pipeline, "predict_proba"):
            return self.pipeline.predict_proba(texts)
        pred = self.predict(texts)
        classes = list(self.pipeline.classes_)
        out = np.zeros((len(texts), len(classes)), dtype=float)
        for i, p in enumerate(pred):
            out[i, classes.index(p)] = 1.0
        return out

    @property
    def labels(self) -> List[str]:
        return list(self.pipeline.classes_)
