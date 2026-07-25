from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .common import (
    PangeaError,
    atomic_write_json,
    ensure_under,
    load_project,
    load_project_index,
    output_json,
    relative_to_root,
    resolve_root_path,
    root_dir,
    save_project_index,
    slug,
    utc_now,
)

SPACE_DIRS = ("source", "inputs", "workspace", "outputs", "projects", "assets")


def ensure_platform_layout(root: Path) -> None:
    for name in SPACE_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)


def init_project(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    ensure_platform_layout(root)
    project_id = slug(args.project_id)
    source_root = resolve_root_path(root, args.source_root or f"source/{project_id}")
    ensure_under(source_root, root / "source", "源码目录")
    if not source_root.exists() or not source_root.is_dir():
        raise PangeaError(
            f"源码目录不存在: {source_root}；请先把源码放入 pangea-test/source/ 下，PANGEA 不会在源码目录内创建文件"
        )

    input_root = root / "inputs" / project_id
    workspace_root = root / "workspace" / project_id
    output_root = root / "outputs" / project_id
    manifest_dir = root / "projects" / project_id
    for path in (input_root, workspace_root, output_root, manifest_dir):
        path.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": "1.0",
        "project_id": project_id,
        "display_name": args.display_name or project_id,
        "source_roots": [
            {
                "id": slug(args.source_id or source_root.name),
                "path": relative_to_root(source_root, root),
                "access": "read_only",
            }
        ],
        "input_root": relative_to_root(input_root, root),
        "workspace_root": relative_to_root(workspace_root, root),
        "output_root": relative_to_root(output_root, root),
        "asset_profiles": sorted(set(args.asset_profile or ["storage-common"])),
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    manifest_path = manifest_dir / "project.json"
    atomic_write_json(manifest_path, manifest)

    index = load_project_index(root)
    projects = index.setdefault("projects", {})
    projects[project_id] = {"manifest": relative_to_root(manifest_path, root)}
    index["current_project"] = project_id
    save_project_index(root, index)
    output_json({"created": True, "current_project": project_id, "project": manifest})


def select_project(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    project_id = slug(args.project_id)
    index = load_project_index(root)
    if project_id not in index.get("projects", {}):
        raise PangeaError(f"项目未登记: {project_id}")
    index["current_project"] = project_id
    save_project_index(root, index)
    output_json({"current_project": project_id})


def list_projects(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    index = load_project_index(root)
    output_json(index)


def show_project(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    project = load_project(root, args.project_id)
    output_json(project)


def detect_project(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    candidate = resolve_root_path(root, args.path or ".")
    index = load_project_index(root)
    matches: list[dict[str, Any]] = []
    for project_id in index.get("projects", {}):
        project = load_project(root, project_id)
        for source in project.get("source_roots", []):
            source_path = resolve_root_path(root, source["path"])
            try:
                candidate.relative_to(source_path)
            except ValueError:
                continue
            matches.append({"project_id": project_id, "source_id": source["id"], "source_path": str(source_path)})
    output_json({"path": str(candidate), "matches": matches})


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PANGEA project manager")
    p.add_argument("--root")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--project-id", required=True)
    init.add_argument("--display-name")
    init.add_argument("--source-root")
    init.add_argument("--source-id")
    init.add_argument("--asset-profile", action="append")
    init.set_defaults(func=init_project)

    select = sub.add_parser("select")
    select.add_argument("--project-id", required=True)
    select.set_defaults(func=select_project)

    listed = sub.add_parser("list")
    listed.set_defaults(func=list_projects)

    show = sub.add_parser("show")
    show.add_argument("--project-id")
    show.set_defaults(func=show_project)

    detect = sub.add_parser("detect")
    detect.add_argument("--path")
    detect.set_defaults(func=detect_project)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except PangeaError as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 2
