"""OpenCode analysis execution and strict result staging."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from runtime import engine, model


DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


class AnalysisError(RuntimeError):
    pass


ROLE_PROMPTS = {
    "analysis-worker": (
        "The user message contains the complete frozen analysis context and source lines. "
        "Do not call tools, read files, inspect a repository, or seek other context. "
        "Use only the supplied method documents. Produce semantic objects, risk candidates and gaps only; "
        "never produce DFX, canonical risks, SFMEA, scenarios or test cases. Calls to libc or external frameworks prove "
        "only the call and local branch; do not assert undocumented external semantics, numeric exit propagation or "
        "platform effects. Record those as gaps. In every concurrency object, shared_state, coordination and ordering "
        "are each one non-empty JSON string, never arrays; combine multiple facts into one precise string. "
        "Return the required JSON object."
    ),
    "reviewer": (
        "The user message contains the complete analysis and the exact evidence lines available for review. "
        "Do not call tools, read files, inspect a repository, or seek other context. "
        "If the supplied evidence is insufficient, record a finding instead of obtaining more evidence. "
        "Return only the required JSON object."
    ),
    "composer": (
        "The user message contains all frozen slice results and their available evidence lines. "
        "Do not call tools, read files, inspect a repository, or seek other context. "
        "Reconcile slice-local semantics into one module model using only the supplied evidence. "
        "Return only the required JSON object."
    ),
    "dfx-analyst": (
        "The user message contains a frozen module semantic model, exact evidence and the DFX method. "
        "Do not call tools, read files or repeat slice analysis. Return six module assessments with zero or more findings. "
        "For every risk finding, risk_candidate.missing_evidence is always a JSON array of strings; use [] when none is missing."
    ),
    "risk-adjudicator": (
        "The user message contains a small frozen batch of risk candidates and exact evidence. Do not call tools or read files. "
        "Decide each candidate as confirmed or dismissed independently of whether it can be tested. Do not merge risks or produce SFMEA."
    ),
    "risk-analyst": (
        "The user message contains confirmed candidates and completed adjudications. Do not call tools or read files. "
        "Do not reconsider decisions. Merge identical causal chains into canonical risks, preserve blocked risks with missing evidence, "
        "map every confirmed candidate and produce SFMEA for every canonical risk."
    ),
    "test-designer": (
        "The user message contains validated canonical risks and relevant frozen module semantics. Do not call tools or read files. "
        "Derive scenarios and test cases only. Never decide, rewrite or delete a canonical risk. "
        "Do not create executable cases for blocked risks."
    ),
}


def isolated_opencode_environment(role: str) -> dict[str, str]:
    """Resolve one self-contained no-tool Agent without loading project configuration."""
    if role not in ROLE_PROMPTS:
        raise AnalysisError(f"unknown OpenCode role: {role}")
    denied = {
        "*": "deny", "read": "deny", "glob": "deny", "grep": "deny", "bash": "deny",
        "edit": "deny", "write": "deny", "task": "deny", "skill": "deny", "webfetch": "deny",
    }
    config = {
        "agent": {
            role: {
                "description": f"PANGEA v1 isolated {role}",
                "mode": "primary",
                "temperature": 0.1,
                "prompt": ROLE_PROMPTS[role],
                "tools": {name: False for name in denied if name != "*"},
                "permission": denied,
            }
        }
    }
    environment = os.environ.copy()
    environment["OPENCODE_DISABLE_PROJECT_CONFIG"] = "true"
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    environment["OPENCODE_PERMISSION"] = json.dumps(denied, separators=(",", ":"))
    return environment


def parse_opencode_jsonl(output: str, *, role: str = "analysis-worker") -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    texts: list[str] = []
    finish_reasons: list[str] = []
    sessions: set[str] = set()
    tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    tools: list[str] = []
    errors: list[str] = []
    for number, line in enumerate(output.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnalysisError(f"OpenCode emitted invalid JSONL at line {number}: {exc}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise AnalysisError(f"OpenCode event {number} is invalid")
        events.append(event)
        if isinstance(event.get("sessionID"), str):
            sessions.add(event["sessionID"])
        event_type = event["type"]
        part = event.get("part")
        if event_type == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif event_type == "tool_use" and isinstance(part, dict):
            tools.append(str(part.get("tool", "unknown")))
        elif event_type == "step_finish" and isinstance(part, dict):
            reason = part.get("reason")
            usage = part.get("tokens")
            cache = usage.get("cache") if isinstance(usage, dict) else None
            if not isinstance(reason, str) or not isinstance(usage, dict) or not isinstance(cache, dict):
                raise AnalysisError("OpenCode step_finish is missing reason or token usage")
            values = [usage.get("input"), usage.get("output"), usage.get("reasoning"), cache.get("read"), cache.get("write")]
            if any(type(value) not in {int, float} or value < 0 for value in values):
                raise AnalysisError("OpenCode step_finish has invalid token usage")
            finish_reasons.append(reason)
            for key, value in zip(tokens, values):
                tokens[key] += int(value)
        elif event_type == "error":
            errors.append(str(event.get("error", "unknown OpenCode error")))
    if not events:
        raise AnalysisError("OpenCode emitted no native events")
    if errors:
        raise AnalysisError("OpenCode error event: " + "; ".join(errors))
    if tools:
        raise AnalysisError(f"{role} called tools: " + ", ".join(tools))
    if finish_reasons != ["stop"]:
        raise AnalysisError(f"{role} finish reason is not stop: " + repr(finish_reasons))
    final_text = "".join(texts).strip()
    if not final_text:
        raise AnalysisError(f"{role} returned empty final text")
    return {
        "events": events,
        "session_ids": sorted(sessions),
        "finish_reasons": finish_reasons,
        "tokens": tokens,
        "final_text": final_text,
    }


def preview_opencode_jsonl(output: str) -> dict[str, Any]:
    """Preserve native evidence even when strict validation later rejects the execution."""
    events: list[dict[str, Any]] = []
    sessions: set[str] = set()
    finish_reasons: list[str] = []
    tools: list[str] = []
    tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    for number, line in enumerate(output.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            events.append({"type": "invalid_jsonl", "line": number, "text": line})
            continue
        if not isinstance(event, dict):
            events.append({"type": "invalid_event", "line": number, "value": event})
            continue
        events.append(event)
        if isinstance(event.get("sessionID"), str):
            sessions.add(event["sessionID"])
        part = event.get("part")
        if event.get("type") == "tool_use" and isinstance(part, dict):
            tools.append(str(part.get("tool", "unknown")))
        if event.get("type") == "step_finish" and isinstance(part, dict):
            if isinstance(part.get("reason"), str):
                finish_reasons.append(part["reason"])
            usage = part.get("tokens")
            cache = usage.get("cache") if isinstance(usage, dict) else None
            values = [
                usage.get("input") if isinstance(usage, dict) else None,
                usage.get("output") if isinstance(usage, dict) else None,
                usage.get("reasoning") if isinstance(usage, dict) else None,
                cache.get("read") if isinstance(cache, dict) else None,
                cache.get("write") if isinstance(cache, dict) else None,
            ]
            if all(type(value) in {int, float} and value >= 0 for value in values):
                for key, value in zip(tokens, values):
                    tokens[key] += int(value)
    return {
        "events": events,
        "session_ids": sorted(sessions),
        "finish_reasons": finish_reasons,
        "tokens": tokens,
        "tools": tools,
    }


def next_execution_path(directory: Path, call_id: str) -> Path:
    """Allocate an append-only receipt path, including compatibility with the first v1 draft."""
    execution_directory = directory / "executions"
    attempt = 1
    if (execution_directory / f"{call_id}.json").exists():
        attempt = 2
    for path in execution_directory.glob(f"{call_id}-attempt-*.json"):
        try:
            attempt = max(attempt, int(path.stem.rsplit("-", 1)[-1]) + 1)
        except ValueError:
            continue
    return execution_directory / f"{call_id}-attempt-{attempt:03d}.json"


def _one_json_object(text: str, *, role: str = "analysis-worker") -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise AnalysisError(f"{role} final JSON contains duplicate key: {key}")
            value[key] = item
        return value

    decoder = json.JSONDecoder(object_pairs_hook=unique_object)
    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"{role} final text is not JSON: {exc}") from exc
    if text[end:].strip():
        raise AnalysisError(f"{role} final text contains data after the JSON object")
    if not isinstance(value, dict):
        raise AnalysisError(f"{role} final JSON must be an object")
    return value


def _prompt(context: dict[str, Any]) -> str:
    return (
        "Do not call tools or read files: the complete frozen context is embedded below. "
        "Read every source line before drawing conclusions. "
        "Use Simplified Chinese for human-readable content. Declare only evidence-backed relationships; "
        "write uncertainty as a gap. Apply only the embedded methods whose evidence signals are relevant, and list them. "
        "Copy every applied_skills.name byte-for-byte from context.methods[].name; never abbreviate or translate it. "
        "Before returning, verify internally that the complete response parses once as JSON and that every top-level "
        "key appears exactly once. Do not emit partial JSON even when the analysis is long. "
        "The first output character must be { and the last must be }; never emit Markdown or a code fence. "
        "Return exactly one JSON object that follows context.contract.\n\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def invoke_opencode(
    root: Path,
    context: dict[str, Any],
    *,
    model_name: str = DEFAULT_MODEL,
    timeout_seconds: int = 1800,
) -> subprocess.CompletedProcess[str]:
    command = [
        "opencode", "run", "--pure", "--agent", "analysis-worker", "--model", model_name,
        "--format", "json", "--title", f"PANGEA {context['run_id']} {context['slice']['slice_id']}",
        _prompt(context),
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="pangea-opencode-worker-") as working_directory:
            return subprocess.run(
                command,
                cwd=working_directory,
                env=isolated_opencode_environment("analysis-worker"),
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError(f"OpenCode analysis timed out after {timeout_seconds}s") from exc


def _stage_result(
    root: Path,
    run_id: str,
    slice_id: str,
    process: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    directory = engine.run_dir(root, run_id)
    receipt_path = next_execution_path(directory, slice_id)
    context = engine.read_json(directory / "contexts" / f"{slice_id}.json")
    receipt: dict[str, Any] = {
        "type": "opencode_execution",
        "run_id": run_id,
        "slice_id": slice_id,
        "returncode": process.returncode,
        "stderr": process.stderr,
        "raw_output": process.stdout,
        **preview_opencode_jsonl(process.stdout),
        "status": "failed",
        "error": None,
    }
    try:
        if process.returncode != 0:
            raise AnalysisError(f"OpenCode exited with code {process.returncode}: {process.stderr.strip()}")
        parsed = parse_opencode_jsonl(process.stdout)
        receipt.update({key: parsed[key] for key in ("events", "session_ids", "finish_reasons", "tokens")})
        value = _one_json_object(parsed["final_text"])
        result = model.validate_slice_result(value, context)
        receipt["status"] = "succeeded"
        engine.atomic_write_json(directory / "results" / f"{slice_id}.json", result)
        return result
    except (AnalysisError, model.ContractError) as exc:
        receipt["error"] = str(exc)
        raise AnalysisError(str(exc)) from exc
    finally:
        engine.atomic_write_json(receipt_path, receipt)


def analyze(
    root: Path,
    run_id: str,
    *,
    model_name: str = DEFAULT_MODEL,
    timeout_seconds: int = 1800,
    runner: Callable[..., subprocess.CompletedProcess[str]] = invoke_opencode,
) -> list[dict[str, Any]]:
    run = engine.load_run(root, run_id)
    if run["state"] == "planned":
        engine.transition(root, run_id, "planned", "analyzing")
    elif run["state"] != "analyzing":
        raise AnalysisError(f"Run state is {run['state']}, expected planned or analyzing")
    directory = engine.run_dir(root, run_id)
    plan = engine.read_json(directory / "plan.json")
    results: list[dict[str, Any]] = []
    for item in plan["slices"]:
        if item["status"] == "completed":
            results.append(engine.read_json(directory / "results" / f"{item['slice_id']}.json"))
            continue
        context = engine.read_json(directory / "contexts" / f"{item['slice_id']}.json")
        try:
            process = runner(root, context, model_name=model_name, timeout_seconds=timeout_seconds)
            result = _stage_result(root, run_id, item["slice_id"], process)
            item["status"] = "completed"
            item["error"] = None
            results.append(result)
        except AnalysisError as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            engine.atomic_write_json(directory / "plan.json", plan)
            progress = {
                "path": "plan.json",
                "total": len(plan["slices"]),
                "completed": sum(row["status"] == "completed" for row in plan["slices"]),
                "failed": sum(row["status"] == "failed" for row in plan["slices"]),
            }
            engine.update_metadata(
                root, run_id, expected_state="analyzing", changes={"plan": progress},
                event="slice_failed", details={"slice_id": item["slice_id"], "error": str(exc)},
            )
            engine.fail(root, run_id, str(exc))
            raise
        engine.atomic_write_json(directory / "plan.json", plan)
        progress = {
            "path": "plan.json",
            "total": len(plan["slices"]),
            "completed": sum(row["status"] == "completed" for row in plan["slices"]),
            "failed": sum(row["status"] == "failed" for row in plan["slices"]),
        }
        engine.update_metadata(
            root, run_id, expected_state="analyzing", changes={"plan": progress},
            event="slice_completed", details={"slice_id": item["slice_id"]},
        )
    return results
