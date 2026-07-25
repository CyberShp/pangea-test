from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PangeaError(RuntimeError):
    """User-facing deterministic tooling error."""


def root_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env_root = os.environ.get("PANGEA_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, *, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise PangeaError(f"文件不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PangeaError(f"JSON 无效: {path}: {exc}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def slug(value: str) -> str:
    value = re.sub(r"[\\/:\s]+", "-", value.strip().lower())
    value = re.sub(r"[^0-9a-z\u4e00-\u9fff._-]+", "-", value)
    value = value.strip("-.")
    if not value:
        raise PangeaError("标识不能为空")
    return value


def ensure_under(path: Path, parent: Path, label: str) -> Path:
    path = path.resolve()
    parent = parent.resolve()
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise PangeaError(f"{label} 必须位于 {parent} 下: {path}") from exc
    return path


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def resolve_root_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def load_project_index(root: Path) -> dict[str, Any]:
    return read_json(
        root / "projects" / "index.json",
        default={"schema_version": "1.0", "current_project": None, "projects": {}},
    )


def save_project_index(root: Path, index: dict[str, Any]) -> None:
    atomic_write_json(root / "projects" / "index.json", index)


def load_project(root: Path, project_id: str | None = None) -> dict[str, Any]:
    index = load_project_index(root)
    resolved_id = project_id or index.get("current_project")
    if not resolved_id:
        raise PangeaError("尚未选择项目；请先创建或选择项目")
    entry = index.get("projects", {}).get(resolved_id)
    if not entry:
        raise PangeaError(f"项目未登记: {resolved_id}")
    manifest = resolve_root_path(root, entry["manifest"])
    project = read_json(manifest)
    project["_manifest_path"] = str(manifest)
    return project


def output_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))
