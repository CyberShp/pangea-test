from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .common import PangeaError, atomic_write_json, output_json, relative_to_root, root_dir, sha256_file, slug, utc_now

ASSET_DIR_TYPES = {
    "feature-knowledge": "feature_knowledge",
    "test-cases": "test_case",
    "experience": "experience",
    "failure-modes": "failure_mode",
    "defect-patterns": "defect_pattern",
    "observation-points": "observation_point",
}


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value.strip("'\"")


def parse_markdown_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    metadata: dict[str, Any] = {}
    current_list: str | None = None
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  - ") and current_list:
            metadata.setdefault(current_list, []).append(raw[4:].strip().strip("'\""))
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        parsed = _parse_scalar(value)
        metadata[key] = parsed
        current_list = key if parsed == "" else None
        if current_list:
            metadata[key] = []
    return metadata


def asset_record(path: Path, root: Path, assets_root: Path) -> dict[str, Any]:
    metadata = json.loads(path.read_text(encoding="utf-8")) if path.suffix.lower() == ".json" else parse_markdown_frontmatter(path)
    directory_type = ASSET_DIR_TYPES.get(path.relative_to(assets_root).parts[0], "other")
    tags = metadata.get("tags", []); tags = [tags] if isinstance(tags, str) else tags
    profiles = metadata.get("profiles", metadata.get("asset_profiles", [])); profiles = [profiles] if isinstance(profiles, str) else profiles
    return {
        "asset_id": metadata.get("asset_id") or slug(f"{directory_type}-{path.stem}"),
        "asset_type": metadata.get("asset_type", directory_type),
        "title": metadata.get("title", path.stem),
        "path": relative_to_root(path, root),
        "tags": sorted(set(tags)),
        "profiles": sorted(set(profiles)),
        "scope": metadata.get("scope", {}),
        "status": metadata.get("status", "draft"),
        "version": str(metadata.get("version", "1.0")),
        "sha256": sha256_file(path),
    }


def index_assets(args: argparse.Namespace) -> None:
    root = root_dir(args.root); assets_root = root / "assets"; assets_root.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(assets_root.rglob("*")):
        if not path.is_file() or path.name in {"catalog.json", "README.md"} or path.suffix.lower() not in {".md", ".json"}:
            continue
        records.append(asset_record(path, root, assets_root))
    atomic_write_json(assets_root / "catalog.json", {"schema_version": "1.0", "generated_at": utc_now(), "assets": records})
    output_json({"count": len(records), "catalog": "assets/catalog.json"})


def _score(item: dict[str, Any], args: argparse.Namespace) -> int:
    score = 2 if item.get("status") == "approved" else 0
    if args.type and item.get("asset_type") == args.type: score += 6
    if args.profile and args.profile in item.get("profiles", []): score += 5
    if args.tag: score += 3 * sum(1 for tag in args.tag if tag in item.get("tags", []))
    if args.query:
        haystack = json.dumps(item, ensure_ascii=False).lower()
        score += 2 * sum(1 for token in re.split(r"\s+", args.query.lower()) if token and token in haystack)
    return score


def search_assets(args: argparse.Namespace) -> None:
    root = root_dir(args.root); catalog_path = root / "assets" / "catalog.json"
    if not catalog_path.exists(): raise PangeaError("资产目录尚未建立；请先执行 asset index")
    results = []
    for item in json.loads(catalog_path.read_text(encoding="utf-8")).get("assets", []):
        if args.type and item.get("asset_type") != args.type: continue
        if args.profile and args.profile not in item.get("profiles", []): continue
        if args.status and item.get("status") != args.status: continue
        if args.tag and not all(tag in item.get("tags", []) for tag in args.tag): continue
        score = _score(item, args)
        if args.query and score <= (2 if item.get("status") == "approved" else 0): continue
        results.append({**item, "score": score})
    results.sort(key=lambda item: (-item["score"], item["asset_id"]))
    output_json({"matches": results[:args.limit]})


def show_asset(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    catalog = json.loads((root / "assets" / "catalog.json").read_text(encoding="utf-8"))
    match = next((item for item in catalog.get("assets", []) if item["asset_id"] == args.asset_id), None)
    if not match: raise PangeaError(f"资产不存在: {args.asset_id}")
    output_json(match)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PANGEA asset registry"); p.add_argument("--root")
    sub = p.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index"); index.set_defaults(func=index_assets)
    search = sub.add_parser("search"); search.add_argument("--type"); search.add_argument("--profile"); search.add_argument("--tag", action="append")
    search.add_argument("--query"); search.add_argument("--status", default="approved"); search.add_argument("--limit", type=int, default=10); search.set_defaults(func=search_assets)
    show = sub.add_parser("show"); show.add_argument("--asset-id", required=True); show.set_defaults(func=show_asset)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args); return 0
    except (PangeaError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr); return 2
