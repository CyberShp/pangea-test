"""Run creation and the only PANGEA v1 state machine."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime import model


class RunError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", value) is None:
        raise RunError("run_id must use letters, digits, dot, underscore or hyphen")
    return value


def run_dir(root: Path, run_id: str) -> Path:
    return root.resolve() / "pangea-data" / "runs" / _run_id(run_id)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunError(f"cannot read JSON artifact {path}: {exc}") from exc


def load_run(root: Path, run_id: str) -> dict[str, Any]:
    value = read_json(run_dir(root, run_id) / "run.json")
    try:
        return model.validate_run(value)
    except model.ContractError as exc:
        raise RunError(str(exc)) from exc


def create_run(root: Path, run_id: str, *, repository: str, scope: str, target: str) -> dict[str, Any]:
    directory = run_dir(root, run_id)
    if directory.exists():
        raise RunError(f"Run already exists: {run_id}")
    timestamp = _now()
    value = model.new_run(run_id, {
        "kind": "module",
        "repository": repository,
        "scope": scope,
        "target": target,
    }, timestamp)
    directory.mkdir(parents=True)
    atomic_write_json(directory / "run.json", value)
    append_event(directory, "run_created", {"state": "draft"}, timestamp=timestamp)
    return value


def append_event(directory: Path, event: str, details: dict[str, Any], *, timestamp: str | None = None) -> None:
    row = {"time": timestamp or _now(), "event": event, "details": details}
    with (directory / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _save(directory: Path, value: dict[str, Any], event: str, details: dict[str, Any]) -> dict[str, Any]:
    value["revision"] += 1
    value["updated_at"] = _now()
    model.validate_run(value)
    atomic_write_json(directory / "run.json", value)
    append_event(directory, event, details, timestamp=value["updated_at"])
    return value


def transition(root: Path, run_id: str, expected: str, target: str) -> dict[str, Any]:
    directory = run_dir(root, run_id)
    value = load_run(root, run_id)
    if value["state"] != expected:
        raise RunError(f"Run state is {value['state']}, expected {expected}")
    if model.NORMAL_TRANSITIONS.get(expected) != target:
        raise RunError(f"illegal state transition: {expected} -> {target}")
    value["state"] = target
    value["failed_stage"] = None
    value["last_error"] = None
    return _save(directory, value, "state_changed", {"from": expected, "to": target})


def fail(root: Path, run_id: str, message: str) -> dict[str, Any]:
    directory = run_dir(root, run_id)
    value = load_run(root, run_id)
    if value["state"] in {"draft", "completed", "failed"}:
        raise RunError(f"cannot fail Run from state {value['state']}")
    failed_stage = value["state"]
    value["state"] = "failed"
    value["failed_stage"] = failed_stage
    value["last_error"] = message
    return _save(directory, value, "run_failed", {"stage": failed_stage, "error": message})


def retry(root: Path, run_id: str) -> dict[str, Any]:
    directory = run_dir(root, run_id)
    value = load_run(root, run_id)
    if value["state"] == "reviewing" and value["review"]["verdict"] == "fail":
        value["state"] = "analyzing"
        value["analysis"] = {"path": None, "revision": None}
        value["review"] = {"path": None, "analysis_revision": None, "verdict": None}
        value["report"] = {"markdown": None, "html": None, "analysis_revision": None}
        value["failed_stage"] = None
        value["last_error"] = None
        _reset_failed_review_slices(directory)
        return _save(directory, value, "run_rework_requested", {"to": "analyzing"})
    if value["state"] != "failed" or value["failed_stage"] is None:
        raise RunError("only a failed Run or a Run with failed review can be retried")
    target = value["failed_stage"]
    value["state"] = target
    value["failed_stage"] = None
    value["last_error"] = None
    return _save(directory, value, "run_retried", {"to": target})


def _reset_failed_review_slices(directory: Path) -> None:
    """Reset only slices named by error findings; downstream checkpoints are invalidated."""
    plan = read_json(directory / "plan.json")
    review = read_json(directory / "review.json")
    object_ids = {
        identifier
        for finding in review["findings"] if finding["severity"] == "error"
        for identifier in finding["object_ids"]
    }
    prefixes = {identifier.split("-", 1)[0] for identifier in object_ids if re.fullmatch(r"S\d{3}-.*", identifier)}
    if not prefixes:
        prefixes = {row["slice_id"] for row in plan["slices"]}
    for row in plan["slices"]:
        if row["slice_id"] in prefixes:
            row["status"] = "pending"
            row["error"] = None
    atomic_write_json(directory / "plan.json", plan)
    for name in (
        "module-result.json", "dfx-result.json", "risk-result.json", "risk-synthesis-result.json",
        "test-design-result.json", "analysis.json", "review.json",
    ):
        path = directory / name
        if path.exists():
            path.unlink()
    for path in directory.glob("risk-adjudication-*-result.json"):
        path.unlink()


def reopen_review(root: Path, run_id: str) -> dict[str, Any]:
    """Invalidate only review/report metadata while preserving the frozen analysis."""
    directory = run_dir(root, run_id)
    value = load_run(root, run_id)
    if value["state"] != "completed" or value["analysis"]["path"] is None:
        raise RunError("only a completed Run with analysis can be re-reviewed")
    value["state"] = "reviewing"
    value["review"] = {"path": None, "analysis_revision": None, "verdict": None}
    value["report"] = {"markdown": None, "html": None, "analysis_revision": None}
    value["failed_stage"] = None
    value["last_error"] = None
    return _save(directory, value, "review_reopened", {"analysis_revision": value["analysis"]["revision"]})


def commit_stage(
    root: Path,
    run_id: str,
    *,
    expected: str,
    target: str,
    changes: dict[str, Any],
    event: str,
) -> dict[str, Any]:
    """Commit one stage and its run metadata as a single run.json transition."""
    directory = run_dir(root, run_id)
    value = load_run(root, run_id)
    if value["state"] != expected:
        raise RunError(f"Run state is {value['state']}, expected {expected}")
    if model.NORMAL_TRANSITIONS.get(expected) != target:
        raise RunError(f"illegal state transition: {expected} -> {target}")
    protected = {"format_version", "run_id", "revision", "state", "created_at", "updated_at"}
    if protected.intersection(changes):
        raise RunError("stage changes cannot replace protected run fields")
    unknown = set(changes) - set(value)
    if unknown:
        raise RunError("stage changes contain unknown run fields: " + ", ".join(sorted(unknown)))
    value.update(changes)
    value["state"] = target
    value["failed_stage"] = None
    value["last_error"] = None
    return _save(directory, value, event, {"from": expected, "to": target})


def update_metadata(
    root: Path,
    run_id: str,
    *,
    expected_state: str,
    changes: dict[str, Any],
    event: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    directory = run_dir(root, run_id)
    value = load_run(root, run_id)
    if value["state"] != expected_state:
        raise RunError(f"Run state is {value['state']}, expected {expected_state}")
    protected = {"format_version", "run_id", "revision", "state", "created_at", "updated_at"}
    if protected.intersection(changes) or set(changes) - set(value):
        raise RunError("metadata changes are invalid")
    value.update(changes)
    return _save(directory, value, event, details)
