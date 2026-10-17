"""Task-disjoint feature/label splitting with reproducible manifests."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SplitConfig:
    seed: int = 20261017
    train_fraction: float = 0.6
    validation_fraction: float = 0.2
    test_fraction: float = 0.2
    group_field: str = "task_id"

    def __post_init__(self) -> None:
        fractions = (self.train_fraction, self.validation_fraction, self.test_fraction)
        if any(f <= 0 for f in fractions) or abs(sum(fractions) - 1.0) > 1e-9:
            raise ValueError("split fractions must be positive and sum to 1")
        if self.group_field not in {"task_id", "repository_id"}:
            raise ValueError("group_field must be task_id or repository_id")


@dataclass(frozen=True, slots=True)
class SplitResult:
    manifest: dict[str, Any]
    output_dir: Path


def validate_split_manifest(manifest: dict[str, Any]) -> None:
    """Fail closed if any group is present in more than one split."""

    groups = manifest.get("groups", {})
    names = ("train", "validation", "test")
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            overlap = set(groups.get(left, ())) & set(groups.get(right, ()))
            if overlap:
                raise ValueError(
                    f"group leakage between {left} and {right}: {sorted(overlap)}"
                )


def split_feature_label_files(
    feature_path: Path,
    label_path: Path,
    output_dir: Path,
    config: SplitConfig | None = None,
) -> SplitResult:
    """Split paired JSONL files by group before exposing action rows."""

    settings = config or SplitConfig()
    features = _read_jsonl(feature_path)
    labels = _read_jsonl(label_path)
    if len(features) != len(labels):
        raise ValueError("feature and label row counts differ")
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen_keys: set[tuple[str, int]] = set()
    for feature, label in zip(features, labels, strict=True):
        key = (str(feature.get("scenario_id")), int(feature.get("sequence", -1)))
        label_key = (str(label.get("scenario_id")), int(label.get("sequence", -1)))
        if key != label_key or key in seen_keys:
            raise ValueError("feature/label identity mismatch or duplicate action")
        group = feature.get(settings.group_field) or label.get(settings.group_field)
        if not isinstance(group, str) or not group:
            raise ValueError(f"missing required split group: {settings.group_field}")
        seen_keys.add(key)
        pairs.append((feature, label))
    if len({feature[settings.group_field] for feature, _ in pairs}) < 3:
        raise ValueError("at least three groups are required for train/validation/test")

    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for pair in pairs:
        groups.setdefault(str(pair[0][settings.group_field]), []).append(pair)
    ordered_groups = list(groups)
    random.Random(settings.seed).shuffle(ordered_groups)
    targets = {
        "train": settings.train_fraction * len(pairs),
        "validation": settings.validation_fraction * len(pairs),
        "test": settings.test_fraction * len(pairs),
    }
    assignments: dict[str, str] = {}
    counts = {name: 0 for name in targets}
    # Assign whole groups to the split furthest below its target. This is
    # deterministic and keeps every action from a task in exactly one split.
    for group in ordered_groups:
        split = min(targets, key=lambda name: (counts[name] - targets[name], name))
        assignments[group] = split
        counts[split] += len(groups[group])

    output_dir.mkdir(parents=True, exist_ok=True)
    split_groups: dict[str, list[str]] = {name: [] for name in targets}
    row_counts: dict[str, int] = {name: 0 for name in targets}
    # Truncate prior outputs so rerunning with the same directory is idempotent.
    for split in targets:
        (output_dir / f"{split}_features.jsonl").write_text("", encoding="utf-8")
        (output_dir / f"{split}_labels.jsonl").write_text("", encoding="utf-8")
    for group, split in assignments.items():
        split_groups[split].append(group)
        row_counts[split] += len(groups[group])
        _write_jsonl(
            output_dir / f"{split}_features.jsonl",
            (feature for feature, _ in groups[group]),
            append=True,
        )
        _write_jsonl(
            output_dir / f"{split}_labels.jsonl",
            (label for _, label in groups[group]),
            append=True,
        )
    for groups_for_split in split_groups.values():
        groups_for_split.sort()
    overlap = {
        "train_validation": sorted(
            set(split_groups["train"]) & set(split_groups["validation"])
        ),
        "train_test": sorted(
            set(split_groups["train"]) & set(split_groups["test"])
        ),
        "validation_test": sorted(
            set(split_groups["validation"]) & set(split_groups["test"])
        ),
    }
    if any(overlap.values()):
        raise AssertionError("group leakage detected while creating split")
    manifest = {
        "split_schema_version": "1.0.0",
        "seed": settings.seed,
        "group_field": settings.group_field,
        "fractions": asdict(settings),
        "groups": split_groups,
        "row_counts": row_counts,
        "group_leakage": overlap,
        "feature_sha256": _sha256(feature_path),
        "label_sha256": _sha256(label_path),
        "source_feature_file": feature_path.name,
        "source_label_file": label_path.name,
    }
    (output_dir / "split_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    validate_split_manifest(manifest)
    return SplitResult(manifest=manifest, output_dir=output_dir)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _write_jsonl(path: Path, rows: Any, *, append: bool = False) -> None:
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
