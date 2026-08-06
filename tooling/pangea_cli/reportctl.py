from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime import converters, reporting
from runtime import data_runtime
from .common import output_json


def _is_run_path(path: Path) -> bool:
    """Return whether a physical path is owned by a PANGEA Run."""
    for parent in path.parents:
        if parent.name == "runs" and parent.parent.name == "pangea-data":
            try:
                return bool(path.relative_to(parent).parts)
            except ValueError:
                return False
    return False


def _preview_output_dir(value: str) -> Path:
    """Admit one new directory outside the Run-owned artifact tree."""
    destination = Path(value).expanduser()
    # exists() follows links, while is_symlink() also catches dangling links.
    if destination.exists() or destination.is_symlink():
        raise reporting.ReportError(f"预览输出目录必须尚不存在: {destination}")

    resolved = destination.resolve()
    if _is_run_path(resolved) or _is_run_path(resolved.parent):
        raise reporting.ReportError(
            f"拒绝写入 pangea-data Run 目录；正式报告只能由 runctl finalize-v2 生成: {destination}"
        )
    return destination


def convert(args: argparse.Namespace) -> None:
    result = converters.convert_document(args.source, args.output_dir)
    destination = Path(args.output)
    converters.write_markdown(result, destination)
    output_json({"status": result.status, "markdown": str(destination), "assets": [str(item) for item in result.assets]})


def render(args: argparse.Namespace) -> None:
    model_path = Path(args.model).expanduser()
    if model_path.is_symlink():
        raise reporting.ReportError(f"拒绝符号链接报告模型: {model_path}")
    output_dir = _preview_output_dir(args.output_dir)
    model = json.loads(model_path.read_text(encoding="utf-8"))
    if not isinstance(model, dict): raise reporting.ReportError("报告模型根节点必须为对象")
    markdown, page = reporting.write_report(model, output_dir)
    output_json({"markdown": str(markdown), "html": str(page)})


def import_catalog(args: argparse.Namespace) -> None:
    from .common import root_dir
    output_json(data_runtime.convert_catalog(root_dir(args.root)))


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA 离线文档与报告工具")
    sub = parser.add_subparsers(dest="command", required=True)
    document = sub.add_parser("convert"); document.add_argument("source"); document.add_argument("--output", required=True); document.add_argument("--output-dir"); document.set_defaults(func=convert)
    report = sub.add_parser("render"); report.add_argument("--model", required=True); report.add_argument("--output-dir", required=True); report.set_defaults(func=render)
    catalog = sub.add_parser("import-catalog"); catalog.add_argument("--root"); catalog.set_defaults(func=import_catalog)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args); return 0
    except (OSError, json.JSONDecodeError, converters.ConversionError, reporting.ReportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
