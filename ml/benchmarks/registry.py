"""Local content-addressed model/data registry for Phase 7."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REGISTRY_SCHEMA_VERSION = "1.0.0"


def register_training_run(
    *,
    registry_dir: Path,
    model_name: str,
    model_version: str,
    model_artifact: Path,
    evaluation_report: Path,
    split_manifest: Path,
    feature_label_manifest: Path,
    source_files: tuple[Path, ...] = (),
    intended_use: str = "Shadow-mode stopping-risk recommendations only",
    limitations: tuple[str, ...] = (
        "Not approved to change execution outcomes.",
        "Evaluation is limited to task-disjoint deterministic scenarios.",
        "Provider and repository distributions may not represent production traffic.",
    ),
) -> dict[str, Any]:
    """Register immutable artifacts and metadata in a local registry.

    Existing versions are immutable: re-registering the same content is a
    no-op, while attempting to overwrite a version with different content fails.
    """

    _validate_name(model_name, "model_name")
    _validate_name(model_version, "model_version")
    required = (
        model_artifact,
        evaluation_report,
        split_manifest,
        feature_label_manifest,
    )
    if any(not path.is_file() for path in required):
        missing = [str(path) for path in required if not path.is_file()]
        raise FileNotFoundError("registry input missing: " + ", ".join(missing))
    destination = registry_dir / model_name / model_version
    if destination.exists():
        existing = destination / "registration.json"
        if not existing.is_file():
            raise FileExistsError(f"registry version is incomplete: {destination}")
        current = json.loads(existing.read_text(encoding="utf-8"))
        expected = {
            _sha256(path)
            for path in (
                model_artifact,
                evaluation_report,
                split_manifest,
                feature_label_manifest,
            )
        }
        actual = {
            item.get("sha256")
            for item in current.get("artifacts", {}).values()
            if isinstance(item, dict)
        }
        if not expected.issubset(actual):
            raise FileExistsError(f"registry version is immutable: {destination}")
        return current

    artifacts_dir = destination / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=False)
    paths = {
        "model": model_artifact,
        "evaluation": evaluation_report,
        "split_manifest": split_manifest,
        "feature_label_manifest": feature_label_manifest,
    }
    for source in source_files:
        if not source.is_file():
            raise FileNotFoundError(f"registry source file missing: {source}")
        paths[f"source:{source.name}"] = source
    copied: dict[str, dict[str, Any]] = {}
    for role, source in paths.items():
        target = artifacts_dir / source.name
        shutil.copyfile(source, target)
        copied[role] = {
            "file": f"artifacts/{target.name}",
            "sha256": _sha256(target),
            "bytes": target.stat().st_size,
        }
    evaluation = json.loads(evaluation_report.read_text(encoding="utf-8"))
    split = json.loads(split_manifest.read_text(encoding="utf-8"))
    features = json.loads(feature_label_manifest.read_text(encoding="utf-8"))
    registration = {
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        "model_name": model_name,
        "model_version": model_version,
        "registered_at": datetime.now(UTC).isoformat(),
        "code": _code_metadata(),
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "packages": _package_versions(("xgboost", "scikit-learn", "numpy")),
        },
        "data": {
            "feature_schema_version": features.get("feature_schema_version"),
            "label_schema_version": features.get("label_schema_version"),
            "split_schema_version": split.get("split_schema_version"),
            "split_seed": split.get("seed"),
            "group_field": split.get("group_field"),
            "row_counts": split.get("row_counts"),
        },
        "model": {
            "type": evaluation.get("model_type"),
            "calibration": evaluation.get("calibration"),
            "feature_names": evaluation.get("feature_names", []),
        },
        "metrics": evaluation.get("metrics", {}),
        "artifacts": copied,
        "model_card": {
            "intended_use": intended_use,
            "limitations": list(limitations),
            "shadow_only": True,
        },
    }
    (destination / "registration.json").write_text(
        json.dumps(registration, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (destination / "MODEL_CARD.md").write_text(
        _model_card(registration), encoding="utf-8"
    )
    index_path = registry_dir / "registry.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if index_path.exists()
        else {
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "models": [],
        }
    )
    index["models"] = [
        item
        for item in index.get("models", [])
        if not (
            item.get("model_name") == model_name
            and item.get("model_version") == model_version
        )
    ]
    index["models"].append(
        {
            "model_name": model_name,
            "model_version": model_version,
            "path": f"{model_name}/{model_version}",
            "registration_sha256": _sha256(destination / "registration.json"),
        }
    )
    index["models"].sort(key=lambda item: (item["model_name"], item["model_version"]))
    index_path.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return registration


def _input_hashes(*paths: Path) -> dict[str, dict[str, Any]]:
    return {
        path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
        for path in paths
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_name(value: str, field: str) -> None:
    if not value or value in {".", ".."} or any(char in value for char in "/\\"):
        raise ValueError(f"invalid {field}")


def _code_metadata() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        return result.stdout.strip() or None

    commit = run("git", "rev-parse", "HEAD")
    diff = run("git", "diff", "--binary") or ""
    return {
        "git_commit": commit,
        "git_dirty": bool(diff),
        "git_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
    }


def _package_versions(names: tuple[str, ...]) -> dict[str, str | None]:
    return {name: _package_version(name) for name in names}


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _model_card(registration: dict[str, Any]) -> str:
    card = registration["model_card"]
    lines = [
        f"# Model card: {registration['model_name']} {registration['model_version']}",
        "",
        "## Intended use",
        "",
        card["intended_use"],
        "",
        "## Training and evaluation data",
        "",
        f"- Feature schema: `{registration['data']['feature_schema_version']}`",
        f"- Label schema: `{registration['data']['label_schema_version']}`",
        f"- Split schema: `{registration['data']['split_schema_version']}`",
        f"- Group field: `{registration['data']['group_field']}`",
        f"- Split seed: `{registration['data']['split_seed']}`",
        f"- Row counts: `{json.dumps(registration['data']['row_counts'], sort_keys=True)}`",
        "",
        "## Model and metrics",
        "",
        f"- Type: `{registration['model']['type']}`",
        f"- Calibration: `{registration['model']['calibration']}`",
        f"- Metrics: `{json.dumps(registration['metrics'], sort_keys=True)}`",
        "",
        "## Limitations and safety",
        "",
        *[f"- {item}" for item in card["limitations"]],
        "- Shadow-only: learned predictions must not change action outcomes.",
        "",
        "## Reproducibility",
        "",
        f"- Git commit: `{registration['code']['git_commit'] or 'unavailable'}`",
        f"- Git dirty: `{registration['code']['git_dirty']}`",
        f"- Runtime: `{registration['runtime']['python']}` on `{registration['runtime']['platform']}`",
        "- All registered artifacts are listed with SHA-256 hashes in `registration.json`.",
        "",
    ]
    return "\n".join(lines)
