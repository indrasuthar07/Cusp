"""CLI entrypoint to generate Cusp Phase 7 deterministic dataset and manifest."""

import argparse
import sys
from pathlib import Path

# Add services/gateway and root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "services" / "gateway"))
sys.path.insert(0, str(root_dir))

from ml.datasets.generator import generate_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Cusp Phase 7 deterministic scenario dataset")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root_dir / "ml" / "datasets",
        help="Target directory for dataset JSONL and manifest.json",
    )
    args = parser.parse_args()

    print(f"Generating deterministic scenario dataset into {args.output_dir}...")
    manifest = generate_dataset(args.output_dir)
    print("Dataset generation complete!")
    print(f"  Total scenarios : {manifest['total_scenarios']}")
    print(f"  Total actions   : {manifest['total_actions']}")
    print(f"  Unique tasks    : {len(manifest['unique_tasks'])}")
    print(f"  Verdicts        : {manifest['verdict_distribution']}")
    print(f"  Outcomes        : {manifest['run_outcome_distribution']}")
    print(f"  SHA-256         : {manifest['sha256']}")
    print(f"  Manifest        : {args.output_dir / 'dataset_manifest.json'}")


if __name__ == "__main__":
    main()
