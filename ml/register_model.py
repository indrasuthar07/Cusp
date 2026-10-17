"""Register a local Phase 7 model and its provenance artifacts."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "gateway"))
sys.path.insert(0, str(ROOT / "packages" / "cusp-sdk-python"))

from ml.benchmarks.registry import register_training_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Register a Cusp model locally")
    parser.add_argument("--registry-dir", type=Path, required=True)
    parser.add_argument("--model-name", default="cusp-stopping-risk")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--model-artifact", type=Path, required=True)
    parser.add_argument("--evaluation-report", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--feature-label-manifest", type=Path, required=True)
    args = parser.parse_args()
    registration = register_training_run(
        registry_dir=args.registry_dir,
        model_name=args.model_name,
        model_version=args.model_version,
        model_artifact=args.model_artifact,
        evaluation_report=args.evaluation_report,
        split_manifest=args.split_manifest,
        feature_label_manifest=args.feature_label_manifest,
    )
    print(registration)


if __name__ == "__main__":
    main()
