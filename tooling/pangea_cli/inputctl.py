from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import PangeaError, atomic_write_json, load_project, output_json, relative_to_root, resolve_root_path, root_dir, sha256_file, slug, utc_now

ROLE_DIRS = {
    "design": "design", "requirements": "requirement", "requirement": "requirement",
    "coverage": "coverage", "existing-cases": "testcase", "test-cases": "testcase", "cases": "testcase",
    "logs": "log", "pcaps": "pcap", "pcap": "pcap", "mr": "mr", "defects": "defect",
    "reports": "report", "reference": "reference",
}


def infer_role(path: Path, input_root: Path) -> str:
    try:
        first = path.relative_to(input_root).parts[0].lower()
    except (ValueError, IndexError):
        return "other"
    return ROLE_DIRS.get(first, "other")


def version_hint(name: str) -> str | None:
    match = re.search(r"(?:^|[-_\s])(v?\d+(?:\.\d+){0,2}|r\d+)(?:[-_\s.]|$)", name, re.IGNORECASE)
    return match.group(1) if match else None


def artifact_record(path: Path, root: Path, input_root: Path, role: str | None = None, mode: str = "managed") -> dict[str, Any]:
    stat = path.stat()
    checksum = sha256_file(path)
    return {
        "artifact_id": slug(f"{role or infer_role(path, input_root)}-{path.stem}-{checksum[:10]}"),
        "role": role or infer_role(path, input_root),
        "path": relative_to_root(path, root),
        "mode": mode,
        "format": path.suffix.lower().lstrip(".") or "unknown",
        "version_hint": version_hint(path.name),
        "sha256": checksum,
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
        "status": "active",
    }


def _count_roles(items: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        result[item["role"]] = result.get(item["role"], 0) + 1
    return result


def scan_inputs(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    project = load_project(root, args.project_id)
    input_root = resolve_root_path(root, project["input_root"])
    input_root.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for path in sorted(input_root.rglob("*")):
        if not path.is_file() or path.name == "catalog.json" or "snapshots" in path.parts:
            continue
        artifacts.append(artifact_record(path, root, input_root))
    catalog = {"schema_version": "1.0", "project_id": project["project_id"], "generated_at": utc_now(), "artifacts": artifacts}
    catalog_path = input_root / "catalog.json"
    atomic_write_json(catalog_path, catalog)
    output_json({"catalog": relative_to_root(catalog_path, root), "count": len(artifacts), "by_role": _count_roles(artifacts)})


def add_input(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    project = load_project(root, args.project_id)
    input_root = resolve_root_path(root, project["input_root"])
    source = Path(args.path).resolve()
    if not source.exists() or not source.is_file():
        raise PangeaError(f"输入文件不存在: {source}")
    if args.mode == "snapshot":
        target_dir = input_root / "snapshots" / args.role
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        if target.exists() and sha256_file(target) != sha256_file(source):
            target = target_dir / f"{source.stem}-{sha256_file(source)[:8]}{source.suffix}"
        shutil.copy2(source, target)
        managed_path = target
    else:
        managed_path = source
    catalog_path = input_root / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {
        "schema_version": "1.0", "project_id": project["project_id"], "generated_at": utc_now(), "artifacts": []
    }
    record = artifact_record(managed_path, root, input_root, args.role, args.mode)
    catalog["artifacts"] = [item for item in catalog["artifacts"] if item["artifact_id"] != record["artifact_id"]]
    catalog["artifacts"].append(record)
    catalog["generated_at"] = utc_now()
    atomic_write_json(catalog_path, catalog)
    output_json(record)


def list_inputs(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    project = load_project(root, args.project_id)
    catalog_path = resolve_root_path(root, project["input_root"]) / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {"artifacts": []}
    items = catalog.get("artifacts", [])
    if args.role:
        items = [item for item in items if item.get("role") == args.role]
    output_json({"project_id": project["project_id"], "artifacts": items})


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PANGEA input catalog manager")
    p.add_argument("--root")
    sub = p.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan"); scan.add_argument("--project-id"); scan.set_defaults(func=scan_inputs)
    add = sub.add_parser("add"); add.add_argument("--project-id"); add.add_argument("--path", required=True)
    add.add_argument("--role", required=True, choices=sorted(set(ROLE_DIRS.values()) | {"other"}))
    add.add_argument("--mode", choices=["reference", "snapshot"], default="reference"); add.set_defaults(func=add_input)
    listed = sub.add_parser("list"); listed.add_argument("--project-id"); listed.add_argument("--role"); listed.set_defaults(func=list_inputs)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except PangeaError as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 2
