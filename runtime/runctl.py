#!/usr/bin/env python3
"""Thin command line entry point for the single PANGEA v1 workflow."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import analysis, engine, report, review, source


ROOT = Path(__file__).resolve().parents[1]


def _default_run_id(target: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", target).strip("-") or "analysis"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{name}-{stamp}"


def _fail_active(root: Path, run_id: str, message: str) -> None:
    try:
        run = engine.load_run(root, run_id)
        if run["state"] not in {"draft", "failed", "completed"}:
            engine.fail(root, run_id, message)
    except Exception:
        return


def run_pipeline(
    root: Path,
    run_id: str,
    *,
    model_name: str,
    timeout_seconds: int,
    analysis_runner: Callable[..., Any] = analysis.invoke_opencode,
    composition_runner: Callable[..., Any] = review.invoke_composer,
    dfx_runner: Callable[..., Any] = review.invoke_dfx,
    adjudication_runner: Callable[..., Any] = review.invoke_adjudicator,
    risk_runner: Callable[..., Any] = review.invoke_risk,
    design_runner: Callable[..., Any] = review.invoke_designer,
    review_runner: Callable[..., Any] = review.invoke_reviewer,
) -> dict[str, Any]:
    try:
        while True:
            run = engine.load_run(root, run_id)
            state = run["state"]
            if state == "draft":
                engine.transition(root, run_id, "draft", "confirmed")
            elif state == "confirmed":
                source.prepare(root, run_id)
            elif state == "prepared":
                source.plan(root, run_id)
            elif state in {"planned", "analyzing"}:
                analysis.analyze(
                    root,
                    run_id,
                    model_name=model_name,
                    timeout_seconds=timeout_seconds,
                    runner=analysis_runner,
                )
                review.compose(
                    root,
                    run_id,
                    model_name=model_name,
                    timeout_seconds=timeout_seconds,
                    runner=composition_runner,
                    dfx_runner=dfx_runner,
                    adjudication_runner=adjudication_runner,
                    risk_runner=risk_runner,
                    design_runner=design_runner,
                )
            elif state == "composed":
                result = review.review(
                    root,
                    run_id,
                    model_name=model_name,
                    timeout_seconds=timeout_seconds,
                    runner=review_runner,
                )
                if result["verdict"] != "pass":
                    return {"status": "review_failed", "run": engine.load_run(root, run_id), "review": result}
            elif state == "reviewing":
                if run["review"]["verdict"] != "pass":
                    result = review.review(
                        root,
                        run_id,
                        model_name=model_name,
                        timeout_seconds=timeout_seconds,
                        runner=review_runner,
                    )
                    if result["verdict"] != "pass":
                        return {"status": "review_failed", "run": engine.load_run(root, run_id), "review": result}
                paths = report.publish(root, run_id)
                return {"status": "completed", "run": engine.load_run(root, run_id), "reports": paths}
            elif state == "completed":
                return {"status": "completed", "run": run, "reports": run["report"]}
            elif state == "failed":
                raise engine.RunError("Run is failed; execute retry before resume")
            else:
                raise engine.RunError(f"unsupported Run state: {state}")
    except Exception as exc:
        _fail_active(root, run_id, str(exc))
        raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="pangea", description="PANGEA v1 module analysis")
    commands = value.add_subparsers(dest="command", required=True)
    analyze_command = commands.add_parser("analyze", help="create and execute one module analysis Run")
    analyze_command.add_argument("--repository", required=True)
    analyze_command.add_argument("--scope", required=True)
    analyze_command.add_argument("--target", required=True)
    analyze_command.add_argument("--run-id")
    analyze_command.add_argument("--model", default=analysis.DEFAULT_MODEL)
    analyze_command.add_argument("--timeout", type=int, default=1800)
    for name in ("resume", "retry", "status", "review", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--run-id", required=True)
        if name in {"resume", "review"}:
            command.add_argument("--model", default=analysis.DEFAULT_MODEL)
            command.add_argument("--timeout", type=int, default=1800)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    run_id = getattr(args, "run_id", None)
    try:
        if args.command == "analyze":
            run_id = run_id or _default_run_id(args.target)
            engine.create_run(ROOT, run_id, repository=args.repository, scope=args.scope, target=args.target)
            result = run_pipeline(ROOT, run_id, model_name=args.model, timeout_seconds=args.timeout)
        elif args.command == "resume":
            result = run_pipeline(ROOT, run_id, model_name=args.model, timeout_seconds=args.timeout)
        elif args.command == "retry":
            result = {"status": "retried", "run": engine.retry(ROOT, run_id)}
        elif args.command == "status":
            result = {"status": "ok", "run": engine.load_run(ROOT, run_id)}
        elif args.command == "review":
            result = {"status": "reviewed", "review": review.review(
                ROOT, run_id, model_name=args.model, timeout_seconds=args.timeout,
            )}
        elif args.command == "publish":
            result = {"status": "completed", "reports": report.publish(ROOT, run_id)}
        else:
            raise RuntimeError("unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") != "review_failed" else 2
    except Exception as exc:
        print(json.dumps({"status": "error", "run_id": run_id, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
