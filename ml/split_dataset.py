"""Split exported feature and label JSONL files by task."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "gateway"))
sys.path.insert(0, str(ROOT / "packages" / "cusp-sdk-python"))

from ml.benchmarks.splitting import SplitConfig, split_feature_label_files


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create task-disjoint Cusp dataset splits"
    )
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261017)
    args = parser.parse_args()
    result = split_feature_label_files(
        args.features,
        args.labels,
        args.output_dir,
        SplitConfig(seed=args.seed),
    )
    print(result.manifest)


if __name__ == "__main__":
    main()
