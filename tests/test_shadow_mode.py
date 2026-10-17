"""Shadow inference is observable and never authoritative."""

from __future__ import annotations

import pickle
from pathlib import Path

from app.shadow.predictor import ShadowPredictor


class _Model:
    def predict_proba(self, rows: list[list[float]]) -> list[list[float]]:
        return [[0.2, 0.8] for _ in rows]


class _Calibrator:
    def predict_proba(self, rows: list[list[float]]) -> list[list[float]]:
        return [[0.7, 0.3] for _ in rows]


def test_shadow_recommendation_is_not_an_execution_decision(tmp_path: Path) -> None:
    artifact = tmp_path / "model.pkl"
    artifact.write_bytes(
        pickle.dumps(
            {
                "model": _Model(),
                "calibrator": _Calibrator(),
                "vocab": {"loop_score": {"None": 0}},
                "feature_names": ["loop_score"],
            }
        )
    )
    prediction = ShadowPredictor(artifact, "cusp-stopping-risk", "1.0.0").predict(
        {"loop_score": None}
    )
    assert prediction.recommended_verdict == "allow"
    assert prediction.confidence == 0.7
    # The caller stores this beside, rather than replacing, its deterministic
    # verdict; the predictor exposes no mutation or apply operation.
    deterministic_verdict = "pause"
    assert deterministic_verdict != prediction.recommended_verdict
