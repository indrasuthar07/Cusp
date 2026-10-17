"""Fail-closed, optional model inference for shadow recommendations."""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ShadowPrediction:
    recommended_verdict: str
    confidence: float
    model_name: str
    model_version: str
    features: dict[str, Any]


class ShadowPredictor:
    """Loads the registered artifact lazily and never makes execution decisions."""

    def __init__(self, artifact: Path, model_name: str, model_version: str) -> None:
        self.artifact = artifact
        self.model_name = model_name
        self.model_version = model_version
        self._bundle: dict[str, Any] | None = None

    def predict(self, features: dict[str, Any]) -> ShadowPrediction:
        if self._bundle is None:
            with self.artifact.open("rb") as handle:
                loaded = pickle.load(handle)
            if (
                not isinstance(loaded, dict)
                or not {"model", "calibrator", "vocab", "feature_names"}
                <= loaded.keys()
            ):
                raise ValueError("shadow model artifact has an invalid schema")
            self._bundle = loaded
        bundle = self._bundle
        names = bundle["feature_names"]
        vocab = bundle["vocab"]
        row = [[float(vocab[name][str(features.get(name, ""))]) for name in names]]
        raw = float(bundle["model"].predict_proba(row)[0][1])
        probability = float(bundle["calibrator"].predict_proba([[raw]])[0][1])
        probability = max(0.0, min(1.0, probability))
        return ShadowPrediction(
            recommended_verdict="pause" if probability >= 0.5 else "allow",
            confidence=max(probability, 1.0 - probability),
            model_name=self.model_name,
            model_version=self.model_version,
            features={name: features.get(name) for name in names},
        )
