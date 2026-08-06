from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from runtime import data_runtime
from .common import output_json, root_dir


def _json_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise data_runtime.DataRuntimeError(f"{label} 不是有效 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise data_runtime.DataRuntimeError(f"{label} 必须是 JSON 对象")
    return parsed


def _payload(args: argparse.Namespace, label: str) -> dict[str, Any]:
    if args.json:
        return _json_object(args.json, label)
    return _json_object(Path(args.file).read_text(encoding="utf-8"), label)


def initialize(args: argparse.Namespace) -> None:
    output_json({"data_root": str(data_runtime.ensure_layout(root_dir(args.root)))})


def scan(args: argparse.Namespace) -> None:
    output_json(data_runtime.scan_inbox(root_dir(args.root)))


def update_repositories(args: argparse.Namespace) -> None:
    output_json({"repositories": data_runtime.safe_pull_repositories(root_dir(args.root))})


def prepare(args: argparse.Namespace) -> None:
    output_json(data_runtime.session_prepare(root_dir(args.root), args.stale_hours))


def list_incomplete(args: argparse.Namespace) -> None:
    output_json({"runs": data_runtime.incomplete_runs(root_dir(args.root))})


def cleanup(args: argparse.Namespace) -> None:
    output_json(data_runtime.cleanup_stale_tmp(root_dir(args.root), args.stale_hours))


def state(args: argparse.Namespace) -> None:
    output_json(data_runtime.set_run_state(root_dir(args.root), args.run_id, args.state, args.message))


def checkpoint(args: argparse.Namespace) -> None:
    output_json(data_runtime.append_checkpoint(root_dir(args.root), args.run_id, _payload(args, "阶段检查点")))


def risk(args: argparse.Namespace) -> None:
    output_json(data_runtime.upsert_risk(root_dir(args.root), args.run_id, _payload(args, "风险卡")))


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA pangea-data runtime")
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.set_defaults(func=initialize)
    inbox = sub.add_parser("scan-inbox"); inbox.set_defaults(func=scan)
    repos = sub.add_parser("update-repositories"); repos.set_defaults(func=update_repositories)
    prep = sub.add_parser("session-prepare"); prep.add_argument("--stale-hours", type=int, default=24); prep.set_defaults(func=prepare)
    incomplete = sub.add_parser("incomplete-runs"); incomplete.set_defaults(func=list_incomplete)
    clean = sub.add_parser("cleanup-tmp"); clean.add_argument("--stale-hours", type=int, default=24); clean.set_defaults(func=cleanup)
    state_parser = sub.add_parser("set-state"); state_parser.add_argument("--run-id", required=True); state_parser.add_argument("--state", required=True, choices=sorted(data_runtime.STATUS)); state_parser.add_argument("--message", required=True); state_parser.set_defaults(func=state)
    checkpoint_parser = sub.add_parser("checkpoint"); checkpoint_parser.add_argument("--run-id", required=True); _add_payload(checkpoint_parser); checkpoint_parser.set_defaults(func=checkpoint)
    risk_parser = sub.add_parser("upsert-risk"); risk_parser.add_argument("--run-id", required=True); _add_payload(risk_parser); risk_parser.set_defaults(func=risk)
    return parser


def _add_payload(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--json")
    group.add_argument("--file")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (data_runtime.DataRuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
