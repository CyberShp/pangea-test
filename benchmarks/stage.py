"""Stage only public benchmark inputs for an agent evaluation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
ORACLE_DIRECTORY = ROOT / "oracles"
REQUIRED_PUBLIC_FIELDS = {
    "id", "title", "mode", "repository", "source", "revision", "agent_input"
}
REQUIRED_ORACLE_FIELDS = {
    "case_id", "fault_mode", "trigger", "observation", "recovery",
    "evidence_keywords", "scoring",
}


class BenchmarkError(ValueError):
    """Raised when benchmark data cannot be safely staged."""


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest(root: Path = ROOT) -> list[str]:
    """Return validation errors for public cases and their private oracles."""
    errors: list[str] = []
    try:
        manifest = load_manifest(root / "manifest.json")
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read manifest: {exc}"]

    if manifest.get("schema_version") != "1.0":
        errors.append("manifest schema_version must be 1.0")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) < 4:
        errors.append("manifest must contain at least four cases")
        return errors

    identifiers: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            errors.append("case must be an object")
            continue
        missing = REQUIRED_PUBLIC_FIELDS - case.keys()
        case_id = case.get("id", "<missing>")
        if missing:
            errors.append(f"{case_id}: missing public fields {sorted(missing)}")
        if case_id in identifiers:
            errors.append(f"duplicate case id: {case_id}")
        identifiers.add(case_id)
        if case.get("mode") not in {"mr-regression", "module-analysis"}:
            errors.append(f"{case_id}: unsupported mode")
        if not str(case.get("repository", "")).startswith("https://github.com/"):
            errors.append(f"{case_id}: repository must be a GitHub URL")
        source = case.get("source", {})
        if not isinstance(source, dict) or not str(source.get("url", "")).startswith("https://github.com/"):
            errors.append(f"{case_id}: source URL must be a GitHub URL")
        revision = case.get("revision", {})
        if not isinstance(revision, dict) or not revision.get("base_commit"):
            errors.append(f"{case_id}: base_commit is required")

        oracle_path = root / "oracles" / f"{case_id}.json"
        try:
            oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{case_id}: cannot read oracle: {exc}")
            continue
        missing_oracle = REQUIRED_ORACLE_FIELDS - oracle.keys()
        if missing_oracle:
            errors.append(f"{case_id}: missing oracle fields {sorted(missing_oracle)}")
        if oracle.get("case_id") != case_id:
            errors.append(f"{case_id}: oracle case_id mismatch")
        if not isinstance(oracle.get("scoring"), list) or not oracle["scoring"]:
            errors.append(f"{case_id}: oracle scoring must be a nonempty list")
    return errors


def stage_case(case_id: str, destination: Path, root: Path = ROOT) -> Path:
    """Write a self-contained public evaluation input without copying oracles."""
    errors = validate_manifest(root)
    if errors:
        raise BenchmarkError("invalid benchmark manifest: " + "; ".join(errors))
    manifest = load_manifest(root / "manifest.json")
    case = next((item for item in manifest["cases"] if item["id"] == case_id), None)
    if case is None:
        raise BenchmarkError(f"unknown benchmark case: {case_id}")
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    task = case["agent_input"]
    (destination / "TASK.md").write_text(task + "\n", encoding="utf-8")
    shutil.rmtree(destination / "oracles", ignore_errors=True)
    return destination
