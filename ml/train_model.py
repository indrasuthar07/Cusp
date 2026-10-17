"""Train and evaluate the optional Phase 7 XGBoost model."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "gateway"))
sys.path.insert(0, str(ROOT / "packages" / "cusp-sdk-python"))

from ml.benchmarks.training import train_stopping_model


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train calibrated Cusp stopping-risk model"
    )
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(train_stopping_model(args.split_dir, args.output_dir))


if __name__ == "__main__":
    main()
