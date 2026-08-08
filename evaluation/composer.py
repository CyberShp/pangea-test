"""Evaluator-owned composition of a complete R2 PANGEA run.

This module is deliberately an orchestration boundary, not a product agent.
The primary process is supplied by the evaluator as callbacks while every leaf
execution is performed through the sealed isolated-role API.  It therefore
also works with deterministic local test doubles without making any provider
or external request.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
import stat
from pathlib import Path
import tempfile
import re
from typing import Any, Callable, Mapping

from evaluation import benchmark
from runtime import analysis_pipeline, coverage_judge, data_runtime, fragment_runtime, runctl, compact_protocol


class ComposerError(RuntimeError):
    """A composition boundary failed closed."""


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


_CLAIM_ID = re.compile(r"^(?:C|R)-[a-f0-9]{16}$")


def _verify_attestation(path: Path, role: str) -> str:
    value = _json(path)
    try:
        receipt_hash, receipt = fragment_runtime.verify_execution_attestation(value, role)
    except fragment_runtime.FragmentError as exc:
        raise ComposerError("invalid signed execution attestation") from exc
    if receipt.get("agent") != role or path.name != receipt_hash + ".json":
        raise ComposerError("execution attestation role/name binding mismatch")
    return receipt_hash


def _json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ComposerError("managed artifact is not a regular file: " + str(path))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComposerError("invalid managed JSON: " + str(path)) from exc
    if not isinstance(value, dict):
        raise ComposerError("managed JSON must be an object")
    return value


def _payload(path: Path, kind: str, run_id: str) -> dict[str, Any]:
    value = _json(path)
    payload = value.get("payload")
    if value.get("artifact_type") != kind or value.get("run_id") != run_id or not isinstance(payload, dict):
        raise ComposerError("unexpected managed artifact: " + str(path))
    return payload


def _write_exact(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if path.exists():
        if _json(path) != value:
            raise ComposerError("composed receipt conflict")
        return path
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(encoded); handle.flush(); os.fsync(handle.fileno()); temporary = Path(handle.name)
    os.replace(temporary, path); os.chmod(path, 0o400)
    return path


def _run_file_bindings(run: Path) -> list[dict[str, str]]:
    """Hash the exact durable Run file set, excluding the receipt itself."""
    rows: list[dict[str, str]] = []
    for path in sorted(run.rglob("*")):
        if path.is_symlink():
            raise ComposerError("managed Run contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
            raise ComposerError("managed Run contains a special file")
        relative = path.relative_to(run).as_posix()
        if relative == "internal/composed-receipt.json":
            continue
        rows.append({"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()})
    return rows


def _stat_fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _verify_formal_output_closure(root: Path, rows: Any) -> list[dict[str, str]]:
    """Read and verify the exact report.md/report.html directory closure."""
    if (not isinstance(rows, list) or len(rows) != 2
            or any(not isinstance(row, dict) or set(row) != {"path", "sha256"} for row in rows)):
        raise ComposerError("invalid formal output closure")
    parsed: dict[str, dict[str, str]] = {}
    parents: set[Path] = set()
    for row in rows:
        relative = Path(row["path"]) if isinstance(row.get("path"), str) else Path()
        digest = row.get("sha256")
        if (relative.is_absolute() or ".." in relative.parts or relative.as_posix() != row.get("path")
                or relative.name not in {"report.md", "report.html"}
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or relative.name in parsed):
            raise ComposerError("invalid formal output member binding")
        parsed[relative.name] = row; parents.add(relative.parent)
    if set(parsed) != {"report.md", "report.html"} or len(parents) != 1:
        raise ComposerError("formal output members are not one exact report pair")
    relative_parent = next(iter(parents)); directory = root / relative_parent
    try:
        resolved = directory.resolve(strict=True); resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ComposerError("formal output directory escaped evaluation root") from exc
    if directory.is_symlink() or not directory.is_dir():
        raise ComposerError("formal output directory is not managed")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError as exc:
        raise ComposerError("formal output directory cannot be opened exactly") from exc
    try:
        before_directory = _stat_fingerprint(os.fstat(directory_fd))
        members = os.listdir(directory_fd)
        if len(members) != 2 or set(members) != {"report.md", "report.html"}:
            raise ComposerError("formal output directory member closure is not exact")
        verified: list[dict[str, str]] = []
        for name in ("report.md", "report.html"):
            try:
                descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
            except OSError as exc:
                raise ComposerError("formal output is not a stable regular file") from exc
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                    raise ComposerError("formal output is not a non-empty regular file")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                after = os.fstat(descriptor)
                if _stat_fingerprint(before) != _stat_fingerprint(after):
                    raise ComposerError("formal output changed while being read")
                digest = sha256(b"".join(chunks)).hexdigest()
                if digest != parsed[name]["sha256"]:
                    raise ComposerError("composed formal output binding mismatch")
                verified.append(dict(parsed[name]))
            finally:
                os.close(descriptor)
        if before_directory != _stat_fingerprint(os.fstat(directory_fd)) or set(os.listdir(directory_fd)) != set(members):
            raise ComposerError("formal output directory changed while being read")
        return verified
    finally:
        os.close(directory_fd)


def _primary_blocked(receipt: Mapping[str, Any]) -> None:
    """Allow exactly the evaluator's expected primary-to-leaf handoff."""
    if not isinstance(receipt, Mapping):
        raise ComposerError("primary did not return a receipt")
    failures = receipt.get("failures")
    if receipt.get("passed") is not False or failures != ["external_role_execution_required"]:
        raise ComposerError("primary intake must be blocked only for external role execution")
    # The evaluator's receipt shape varies by runner.  These fields make an
    # in-process leaf launch explicit whenever the runner publishes it.
    for key in ("leaf_tasks", "launched_tasks", "subtasks"):
        if key in receipt and receipt[key] not in ([], (), None):
            raise ComposerError("primary may not execute leaf tasks in-process")
    roles = receipt.get("executed_roles")
    if roles is not None and set(roles) != {"primary"}:
        raise ComposerError("primary receipt contains a leaf role")


def _single_active_run(root: Path, resolver: Callable[[Path], list[Path]] | None) -> Path:
    runs_root = root / "pangea-data" / "runs"
    if runs_root.is_symlink() or not runs_root.is_dir():
        raise ComposerError("Run root is not a managed directory")
    entries = list(runs_root.iterdir())
    if any(item.is_symlink() or not item.is_dir() for item in entries) or len(entries) != 1:
        raise ComposerError("exactly one active Run is required")
    actual = entries[0].resolve()
    candidates = resolver(root) if resolver else [actual]
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ComposerError("active Run resolver did not return an exact set")
    candidate = Path(candidates[0])
    if candidate.is_symlink() or candidate.resolve() != actual or actual.parent != runs_root.resolve():
        raise ComposerError("active Run resolver escaped the managed Run root")
    return actual


def _assignments_and_contexts(run: Path) -> tuple[str, list[dict[str, Any]], dict[str, Path]]:
    run_id = run.name
    assignments = _payload(run / "internal/assignment-index.json", "assignment_index", run_id).get("assignments")
    if not isinstance(assignments, list) or not assignments:
        raise ComposerError("Run has no issued assignments")
    by_id = {row.get("fragment_id"): row for row in assignments if isinstance(row, dict)}
    if None in by_id or len(by_id) != len(assignments):
        raise ComposerError("assignment identity is not exact")
    context_root = run / "internal/context-packs"
    if context_root.is_symlink() or not context_root.is_dir():
        raise ComposerError("context-packs is not a managed directory")
    members = list(context_root.iterdir())
    if any(item.is_symlink() or not item.is_dir() for item in members) or {item.name for item in members} != set(by_id):
        raise ComposerError("assignment/context set is not exact")
    contexts: dict[str, Path] = {}
    for directory in members:
        contents = list(directory.iterdir())
        if len(contents) != 1 or contents[0].name != "CONTEXT.json" or contents[0].is_symlink() or not contents[0].is_file() or not stat.S_ISREG(contents[0].stat().st_mode):
            raise ComposerError("context pack is not an exact regular-file closure")
        contexts[directory.name] = contents[0]
    for fid, assignment in by_id.items():
        envelope = _json(contexts[fid]); candidate = envelope.get("payload", {}).get("candidate")
        if (not isinstance(candidate, dict) or envelope.get("payload", {}).get("candidate_sha256") != _hash(candidate)
                or assignment.get("candidate_sha256") != _hash(candidate)
                or candidate.get("context_pack", {}).get("fragment_id") != fid):
            raise ComposerError("context assignment binding mismatch")
    return run_id, [by_id[key] for key in sorted(by_id)], contexts


def _semantic_closure(run: Path, claims: Mapping[str, tuple[dict[str, Any], list[dict[str, Any]]]]) -> list[str]:
    directory = run / "internal/semantic-assessments"
    if directory.is_symlink() or not directory.is_dir():
        raise ComposerError("semantic assessments directory missing")
    members = list(directory.iterdir())
    expected = {claim_id + ".json" for claim_id in claims}
    if any(path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode) for path in members) or {path.name for path in members} != expected:
        raise ComposerError("semantic assessment claim closure is not exact")
    hashes=[]; by_receipt:dict[str,list[str]]={}
    for claim_id, (claim, facts) in claims.items():
        if not _CLAIM_ID.fullmatch(claim_id):
            raise ComposerError("unsafe semantic claim id")
        value=_json(directory/(claim_id+".json")); canonical={key:claim[key] for key in sorted(claim) if key not in {"contribution_id","risk_id"}}
        fact_map={(fact.get("obligation_id"),fact.get("inventory_id"),fact.get("line_start"),fact.get("line_count")):fact for fact in facts}
        keys=claim.get("fact_keys"); excerpts=[]
        try: excerpts=[fact_map[tuple(key)]["excerpt_sha256"] for key in keys]
        except (KeyError, TypeError): raise ComposerError("semantic assessment fact binding is invalid")
        if (set(value)!={"artifact_type","schema_version","claim_id","claim_sha256","fact_keys","source_excerpt_sha256s","supported","reason","auditor_telemetry"}
                or value.get("artifact_type")!="semantic_assessment" or value.get("schema_version")!="1.0" or value.get("claim_id")!=claim_id
                or value.get("claim_sha256")!=_hash(canonical) or value.get("fact_keys")!=keys or value.get("source_excerpt_sha256s")!=excerpts
                or value.get("supported") is not True):
            raise ComposerError("semantic assessment claim/run binding mismatch")
        telemetry=value.get("auditor_telemetry")
        if (not isinstance(telemetry,dict) or set(telemetry)!={"model","input_tokens","output_tokens","finish_reason","valid_json","captured_by","session_id","execution_receipt_sha256"}
                or telemetry.get("model")!=benchmark.DEEPSEEK_MODEL or telemetry.get("finish_reason")!="stop" or telemetry.get("valid_json") is not True
                or telemetry.get("captured_by")!="opencode-runner" or not isinstance(telemetry.get("session_id"),str)
                or not isinstance(telemetry.get("execution_receipt_sha256"),str)):
            raise ComposerError("semantic assessment auditor receipt binding mismatch")
        by_receipt.setdefault(telemetry["execution_receipt_sha256"],[]).append(claim_id)
        hashes.append(sha256((directory/(claim_id+".json")).read_bytes()).hexdigest())
    if not 1<=len(by_receipt)<=compact_protocol.SEMANTIC_AUDITOR_CALL_LIMIT: raise ComposerError("semantic auditor batch count exceeds frozen closure")
    for receipt_hash,ids in by_receipt.items():
        ids=sorted(ids)
        if len(ids)>compact_protocol.AUDITOR_CLAIM_LIMIT: raise ComposerError("semantic auditor batch size exceeds frozen closure")
        entries=[];decisions=[]
        for ordinal,claim_id in enumerate(ids):
            claim,facts=claims[claim_id];keys={tuple(key) for key in claim["fact_keys"]}
            selected=[fact for fact in facts if (fact.get("obligation_id"),fact.get("inventory_id"),fact.get("line_start"),fact.get("line_count")) in keys]
            entries.append({"ordinal":ordinal,"claim":claim,"facts":selected})
            assessment=_json(directory/(claim_id+".json"));decisions.append([ordinal,assessment["supported"],assessment["reason"]])
        attestation=_json(run/"internal/execution-receipts"/(receipt_hash+".json"))
        try: verified,receipt=fragment_runtime.verify_execution_attestation(attestation,"auditor")
        except fragment_runtime.FragmentError as exc: raise ComposerError("semantic auditor attestation invalid") from exc
        bindings=receipt.get("artifact_bindings")
        if (verified!=receipt_hash or not isinstance(bindings,list) or len(bindings)!=1
                or bindings[0].get("name")!="SEMANTIC_BATCH.json"
                or bindings[0].get("payload_sha256")!=_hash({"v":1,"claims":entries})
                or receipt.get("output_payload_sha256")!=_hash({"v":1,"a":decisions})):
            raise ComposerError("semantic auditor batch replay binding mismatch")
    return sorted(hashes)


def _compact_adapter_closure(run:Path,assignments:list[dict[str,Any]],contexts:Mapping[str,Path],
                             verifier:Callable[[Path,str],str]) -> None:
    ids={row["fragment_id"] for row in assignments}
    for directory_name in ("compact-native-outputs","compact-adapter-receipts"):
        directory=run/"internal"/directory_name; members=list(directory.iterdir()) if directory.is_dir() and not directory.is_symlink() else []
        if any(path.is_symlink() or not path.is_file() for path in members) or {path.name for path in members}!={fid+".json" for fid in ids}:
            raise ComposerError("compact adapter member closure is not exact")
    for fid in ids:
        context=_json(contexts[fid]);candidate=context.get("payload",{}).get("candidate",{});pack=candidate.get("context_pack")
        native_envelope=_json(run/"internal/compact-native-outputs"/(fid+".json"));native=native_envelope.get("native")
        if set(native_envelope)!={"artifact_type","schema_version","fragment_id","native"} or native_envelope.get("fragment_id")!=fid: raise ComposerError("compact native output envelope is invalid")
        try: expanded=compact_protocol.expand_native(native,candidate.get("compact_context"),candidate.get("ordinal_map"),pack)
        except compact_protocol.CompactProtocolError as exc: raise ComposerError("compact adapter replay failed") from exc
        managed=_payload(run/"internal/fragments"/(fid+".json"),"fragment_artifact",run.name)
        adapter=_json(run/"internal/compact-adapter-receipts"/(fid+".json"))
        expected={"artifact_type":"compact_adapter_receipt","schema_version":"1.0","fragment_id":fid,
                  "native_output_sha256":_hash(native),"adapter_version":compact_protocol.VERSION,
                  "ordinal_map_sha256":candidate.get("ordinal_map_sha256"),"expanded_fragment_sha256":_hash(expanded),
                  "execution_receipt_sha256":adapter.get("execution_receipt_sha256")}
        receipt_hash=adapter.get("execution_receipt_sha256")
        if managed!=expanded or adapter!=expected or not isinstance(receipt_hash,str): raise ComposerError("compact adapter projection mismatch")
        path=run/"internal/execution-receipts"/(receipt_hash+".json");verifier(path,"analysis-worker")
        receipt=_json(path).get("receipt",{})
        bindings=receipt.get("artifact_bindings") if isinstance(receipt,dict) else None
        if (receipt.get("output_payload_sha256")!=_hash(native) or not isinstance(bindings,list) or len(bindings)!=1
                or bindings[0].get("name")!="COMPACT_CONTEXT.json"
                or bindings[0].get("payload_sha256")!=candidate.get("compact_context_sha256")):
            raise ComposerError("compact adapter execution binding mismatch")


def _fixed_judge_closure(run: Path, run_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    paths={"analysis":run/"internal/analysis-model.json", "report":run/"internal/report-model.json", "coverage_judge":run/"internal/coverage-judge.json"}
    values={name:_json(path) for name,path in paths.items()}
    judge=values["coverage_judge"]
    # Do not accept a candidate-authored PASS-shaped object.  Rebuild the
    # complete, producer-independent R2 input from the managed Run and invoke
    # the same fixed pure Judge used by runctl.
    try:
        contract = data_runtime.read_json(run / "internal/task-contract.json")
        if not isinstance(contract, dict):
            raise ValueError("R2 task contract is missing")
        repositories = contract.get("repositories", [])
        if not isinstance(repositories, list) or not repositories:
            raise ValueError("R2 contract has no repositories")

        def pipeline_payload(path: Path) -> dict[str, Any]:
            value = data_runtime.read_json(path)
            payload = value.get("payload") if isinstance(value, dict) else None
            if not isinstance(payload, dict):
                raise ValueError("invalid R2 pipeline envelope")
            return payload

        inventories = [pipeline_payload(run / f"internal/inventories/{repo}.json") for repo in repositories]
        ledgers = [pipeline_payload(run / f"internal/ledgers/{repo}.json") for repo in repositories]
        assignments = pipeline_payload(run / "internal/assignment-index.json").get("assignments", [])
        fragments = [pipeline_payload(path) for path in sorted((run / "internal/fragments").glob("*.json"))]
        native_outputs = [data_runtime.read_json(path) for path in sorted((run / "internal/compact-native-outputs").glob("*.json"))]
        adapter_receipts = [data_runtime.read_json(path) for path in sorted((run / "internal/compact-adapter-receipts").glob("*.json"))]
        telemetry = [data_runtime.read_json(path) for path in sorted((run / "internal/telemetry").glob("*.json"))]
        semantic = [data_runtime.read_json(path) for path in sorted((run / "internal/semantic-assessments").glob("*.json"))]
        attestations = [data_runtime.read_json(path) for path in sorted((run / "internal/execution-receipts").glob("*.json"))]
        skill_receipts: list[dict[str, Any]] = []
        for path in sorted((run / "internal/context-packs").glob("*/CONTEXT.json")):
            skill_receipts.extend(pipeline_payload(path).get("candidate", {}).get("skill_receipts", []))
        manifests = [pipeline_payload(run / "internal/denominator-state.json"),
                     pipeline_payload(run / "internal/context-publication-state.json")]
        judge_inputs = {
            "run_id": run_id, "inventories": inventories, "ledgers": ledgers,
            "assignments": assignments, "fragments": fragments,
            "native_outputs": native_outputs, "adapter_receipts": adapter_receipts,
            "skill_receipts": skill_receipts, "telemetry": telemetry,
            "semantic_assessments": semantic, "publication_manifests": manifests,
            "execution_attestations": attestations, "artifact_bindings": [],
        }
        judge_inputs["artifact_bindings"] = coverage_judge._expected_artifact_bindings(judge_inputs)
        expected = coverage_judge.judge_r2(judge_inputs)
        expected["input_artifacts"] = runctl._r2_judge_file_bindings(run, repositories)
        runctl.validate(expected, "coverage-judge-r2.schema.json")
        runctl.validate(judge, "coverage-judge-r2.schema.json")
    except (OSError, KeyError, TypeError, ValueError, runctl.RunCtlError) as exc:
        raise ComposerError("Coverage Judge deterministic recomputation failed") from exc
    if judge != expected:
        raise ComposerError("Coverage Judge differs from deterministic recomputation")
    hashes={name:sha256(path.read_bytes()).hexdigest() for name,path in paths.items()}
    inputs=judge.get("input_artifacts")
    required={row["path"]: row["sha256"] for row in expected["input_artifacts"]}
    if (not isinstance(inputs,list) or any(not isinstance(row,dict) or set(row)!={"path","sha256"} or not isinstance(row["path"],str) or not re.fullmatch(r"[a-f0-9]{64}",row["sha256"]) for row in inputs)
            or len({row["path"] for row in inputs})!=len(inputs)
            or judge.get("artifact_type")!="coverage_judge_r2" or judge.get("schema_version")!="1.0" or judge.get("run_id")!=run_id or judge.get("verdict")!="PASS"
            or {row["path"]: row["sha256"] for row in inputs} != required):
        raise ComposerError("Coverage Judge fixed artifact binding mismatch")
    return judge, hashes


def _existing_composed_receipt(root: Path, resolver: Callable[[Path], list[Path]] | None,
                               verifier: Callable[[Path, str], str],
                               execution_closure: Callable[[], Mapping[str, Any]] | None = None,
                               public_bundle_closure: Callable[[], Mapping[str, Any]] | None = None) -> dict[str, Any] | None:
    """Validate and return a completed immutable Run without invoking a runner."""
    runs_root = root / "pangea-data" / "runs"
    if not runs_root.is_dir() or runs_root.is_symlink():
        return None
    initial_entries = list(runs_root.iterdir())
    if not initial_entries:
        return None
    run = _single_active_run(root, resolver)
    path = run / "internal/composed-receipt.json"
    if not path.exists():
        return None
    receipt = _json(path)
    _validate_composed_receipt_shape(
        receipt, require_execution=execution_closure is not None,
        require_public_bundle=public_bundle_closure is not None,
    )
    digest = receipt.get("sha256")
    unsigned = {key: value for key, value in receipt.items() if key != "sha256"}
    if digest != _hash(unsigned) or receipt.get("run_id") != run.name:
        raise ComposerError("composed receipt self-binding mismatch")
    if public_bundle_closure is not None:
        current_bundle = public_bundle_closure()
        if not isinstance(current_bundle, Mapping):
            raise ComposerError("invalid current immutable public bundle closure")
        _validate_public_bundle_binding(current_bundle)
        if receipt.get("public_bundle_binding") != dict(current_bundle):
            raise ComposerError("immutable public bundle binding mismatch")
    _, assignments, contexts = _assignments_and_contexts(run)
    _compact_adapter_closure(run,assignments,contexts,verifier)
    fragments = {
        row["fragment_id"]: _payload(run / "internal/fragments" / (row["fragment_id"] + ".json"),
                                     "fragment_artifact", run.name)
        for row in assignments
    }
    merged = fragment_runtime.merge_fragments([fragments[row["fragment_id"]] for row in assignments])
    claims: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for fragment in fragments.values():
        for family in fragment_runtime.CONTRIBUTION_FAMILIES:
            for claim in fragment["contributions"][family]:
                claims[claim["contribution_id"]] = (claim, fragment["facts"])
        for claim in fragment["risk_cards"]:
            claims[claim["risk_id"]] = (claim, fragment["facts"])
    assessment_hashes = _semantic_closure(run, claims)
    _, fixed_hashes = _fixed_judge_closure(run, run.name)
    receipt_dir = run / "internal/execution-receipts"
    executions: list[tuple[str, str]] = []
    leaf_hashes: list[str] = []
    if receipt_dir.is_dir() and not receipt_dir.is_symlink():
        for attestation_path in sorted(receipt_dir.iterdir()):
            attestation = _json(attestation_path)
            raw = attestation.get("receipt", {})
            role = raw.get("agent") if isinstance(raw, dict) else None
            if role not in {"analysis-worker", "auditor"}:
                raise ComposerError("invalid composed execution role")
            if raw.get("evidence_class") not in {"production", "test-only"}:
                raise ComposerError("invalid composed leaf evidence class")
            receipt_hash = verifier(attestation_path, role)
            executions.append((role, receipt_hash)); leaf_hashes.append(_hash(raw))
    fragment_hashes, telemetry_hashes, attestation_hashes = _exact_leaf_files(
        run, assignments, executions, verifier,
    )
    expected = {
        "leaf_receipt_sha256s": sorted(leaf_hashes),
        "attestation_file_sha256s": sorted(attestation_hashes),
        "fragment_file_sha256s": sorted(fragment_hashes),
        "telemetry_file_sha256s": sorted(telemetry_hashes),
        "semantic_assessment_file_sha256s": assessment_hashes,
        "merged_sha256": merged["sha256"],
        "analysis_file_sha256": fixed_hashes["analysis"],
        "report_file_sha256": fixed_hashes["report"],
        "coverage_judge_file_sha256": fixed_hashes["coverage_judge"],
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ComposerError("composed receipt artifact closure mismatch")
    if receipt.get("run_file_bindings") != _run_file_bindings(run):
        raise ComposerError("composed Run file closure mismatch")
    evaluator_execution = receipt.get("evaluator_execution")
    if evaluator_execution is not None:
        if not isinstance(evaluator_execution, Mapping):
            raise ComposerError("invalid composed evaluator execution closure")
        evaluator_execution = _validate_evaluator_execution(run, evaluator_execution)
        primary_bindings = _authoritative_primary_bindings(evaluator_execution)
        if any(receipt.get(key) != value for key, value in primary_bindings.items()):
            raise ComposerError("composed primary/final output binding mismatch")
        if receipt.get("evidence_class") != evaluator_execution["evidence_class"]:
            raise ComposerError("composed evidence class closure mismatch")
        _validate_leaf_evidence_class(run, evaluator_execution["evidence_class"])
        audit_hashes = _final_audit_closure(run, evaluator_execution, verifier)
        if receipt.get("final_audit_attestation_file_sha256s") != audit_hashes:
            raise ComposerError("composed final auditor attestation binding mismatch")
    if "formal_outputs" in receipt:
        _verify_formal_output_closure(root, receipt["formal_outputs"])
    # Re-entry preserves the historical receipt, but the current invocation
    # still has its own frozen end-to-end deadline.
    if execution_closure is not None:
        current = execution_closure()
        if not isinstance(current, Mapping):
            raise ComposerError("invalid current evaluator execution closure")
    return receipt


def _exact_leaf_files(run: Path, assignments: list[dict[str, Any]], executions: list[tuple[str, str]], verifier: Callable[[Path, str], str]) -> tuple[list[str], list[str], list[str]]:
    ids={row["fragment_id"] for row in assignments}
    output: list[list[str]]=[]
    for directory_name, artifact_type in (("fragments", "fragment_artifact"), ("telemetry", "runner_telemetry")):
        directory=run/"internal"/directory_name
        if directory.is_symlink() or not directory.is_dir(): raise ComposerError(directory_name+" directory missing")
        members=list(directory.iterdir()); expected={fid+".json" for fid in ids}
        if any(path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode) for path in members) or {path.name for path in members}!=expected:
            raise ComposerError(directory_name+" member closure is not exact")
        hashes=[]
        for fid in ids:
            path=directory/(fid+".json")
            value=_payload(path, artifact_type, run.name) if directory_name=="fragments" else _json(path)
            if (directory_name!="fragments" and (value.get("artifact_type")!=artifact_type or value.get("run_id")!=run.name)
                    or value.get("fragment_id")!=fid):
                raise ComposerError(directory_name+" artifact binding mismatch")
            hashes.append(sha256(path.read_bytes()).hexdigest())
        output.append(sorted(hashes))
    directory=run/"internal/execution-receipts"
    expected={receipt_hash+".json" for _,receipt_hash in executions}
    members=list(directory.iterdir()) if directory.is_dir() and not directory.is_symlink() else []
    if not members or any(path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode) for path in members) or {path.name for path in members}!=expected:
        raise ComposerError("execution receipt member closure is not exact")
    attestation=[]
    for role,receipt_hash in executions:
        if verifier(directory/(receipt_hash+".json"),role)!=receipt_hash: raise ComposerError("execution receipt mismatch")
        attestation.append(sha256((directory/(receipt_hash+".json")).read_bytes()).hexdigest())
    return output[0],output[1],sorted(attestation)


def _final_audit_closure(run: Path, execution: Mapping[str, Any],
                         verifier: Callable[[Path, str], str]) -> list[str]:
    phases = execution.get("phases")
    if not isinstance(phases, list):
        raise ComposerError("evaluator execution phase closure missing")
    rows = [row for row in phases if isinstance(row, dict) and row.get("phase") == "report-auditor"]
    if len(rows) != 1 or rows[0].get("role") != "auditor":
        raise ComposerError("exactly one final report auditor is required")
    receipt_hash = rows[0].get("receipt_sha256")
    if not isinstance(receipt_hash, str):
        raise ComposerError("final report auditor receipt binding missing")
    directory = run / "internal/final-audit-execution-receipts"
    members = list(directory.iterdir()) if directory.is_dir() and not directory.is_symlink() else []
    expected = {receipt_hash + ".json"}
    if (any(path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode) for path in members)
            or {path.name for path in members} != expected):
        raise ComposerError("final report auditor receipt closure is not exact")
    path = members[0]
    if verifier(path, "auditor") != receipt_hash:
        raise ComposerError("final report auditor signed receipt mismatch")
    return [sha256(path.read_bytes()).hexdigest()]


def _validate_leaf_evidence_class(run: Path, evidence_class: str) -> None:
    for directory_name in ("execution-receipts", "final-audit-execution-receipts"):
        directory = run / "internal" / directory_name
        if not directory.is_dir() or directory.is_symlink():
            raise ComposerError("leaf evidence directory is invalid")
        for path in directory.iterdir():
            raw = _json(path).get("receipt")
            if not isinstance(raw, dict) or raw.get("evidence_class") != evidence_class:
                raise ComposerError("leaf evidence class closure is inconsistent")


def _recomputed_evaluator_intake_bindings(run: Path) -> list[dict[str, str]]:
    """Recompute the evaluator intake preparation closure from durable Run files."""
    try:
        spec, spec_payload = runctl._stable_unique_json(
            run / runctl.EVALUATOR_INTAKE_RUN_SPEC_RELATIVE,
            "evaluator intake Run spec", read_only=True,
        )
        binding, _ = runctl._stable_unique_json(
            run / runctl.EVALUATOR_INTAKE_BINDING_RELATIVE,
            "evaluator intake Run binding", read_only=True,
        )
        record, _ = runctl._stable_unique_json(
            run / runctl.CONTRACT_RECORD_RELATIVE, "activated contract record",
        )
        confirmed, confirmed_payload = runctl._stable_unique_json(
            run / runctl.EVALUATOR_CONFIRMED_CONTRACT_RELATIVE,
            "evaluator durable confirmed contract", read_only=True,
        )
        task_contract = _json(run / "internal/task-contract.json")
        snapshots = _json(run / "internal/source-snapshots.json")
        manifest = _json(run / "manifest.json")
    except (runctl.RunCtlError, OSError) as exc:
        raise ComposerError("durable evaluator intake binding files are invalid") from exc
    spec_sha256 = sha256(spec_payload).hexdigest()
    try:
        runctl._validate_evaluator_intake_spec_value(spec)
    except runctl.RunCtlError as exc:
        raise ComposerError("durable evaluator intake spec is invalid") from exc
    contract = spec.get("contract")
    repository = spec.get("repository")
    if not isinstance(contract, dict) or not isinstance(repository, dict):
        raise ComposerError("durable evaluator intake projection is invalid")
    expected_binding = {
        "artifact_type": "evaluator_intake_binding", "schema_version": "2.0",
        "owner": "evaluator", "mode": "canonical-case-one-shot-v2",
        "spec_sha256": spec_sha256,
        "confirmed_record_sha256": contract.get("confirmed_sha256"),
        "contract_id": contract.get("id"), "revision": contract.get("revision"),
        "run_id": spec.get("expected_run_id"),
    }
    confirmed_sha256 = runctl._canonical_json_sha256(confirmed)
    rows = snapshots.get("snapshots")
    snapshot = rows[0] if isinstance(rows, list) and len(rows) == 1 else None
    snapshot_manifest = snapshot.get("manifest") if isinstance(snapshot, dict) else None
    evaluator_materialization = (snapshot_manifest.get("evaluator_materialization")
                                 if isinstance(snapshot_manifest, dict) else None)
    expected_materialization = {
        key: repository.get(key) for key in (
            "git_tree", "materialization_sha256", "stage_repository_sha256",
            "source_manifest_sha256",
        )
    }
    case = spec.get("case")
    evaluator_members = {
        path.name for path in (run / "internal").iterdir()
        if path.name.startswith("evaluator-")
    }
    if (evaluator_members != {
            Path(runctl.EVALUATOR_INTAKE_BINDING_RELATIVE).name,
            Path(runctl.EVALUATOR_INTAKE_RUN_SPEC_RELATIVE).name,
            Path(runctl.EVALUATOR_CONFIRMED_CONTRACT_RELATIVE).name,
        }
            or binding != expected_binding
            or manifest.get("run_id") != spec.get("expected_run_id")
            or record.get("status") != "activated"
            or record.get("contract_id") != contract.get("id")
            or record.get("revision") != contract.get("revision")
            or record.get("activation", {}).get("run_id") != spec.get("expected_run_id")
            or record.get("task_contract") != task_contract
            or runctl._canonical_json_sha256(task_contract) != contract.get("task_contract_sha256")
            or confirmed_payload != runctl._canonical_json_bytes(confirmed)
            or confirmed_sha256 != contract.get("confirmed_sha256")
            or confirmed.get("status") != "confirmed"
            or confirmed.get("contract_id") != contract.get("id")
            or confirmed.get("revision") != contract.get("revision")
            or confirmed.get("task_contract") != task_contract
            or not isinstance(case, dict)
            or contract.get("id") != "case-" + str(case.get("sha256", ""))
            or set(snapshots) != {"snapshots", "coverage_gaps"}
            or snapshots.get("coverage_gaps") != []
            or not isinstance(snapshot, dict)
            or set(snapshot) != {"snapshot_id", "repository", "snapshot_dir", "manifest"}
            or snapshot.get("snapshot_id") != repository.get("id")
            or snapshot.get("repository") != repository.get("id")
            or not isinstance(snapshot_manifest, dict)
            or snapshot_manifest.get("repository") != repository.get("id")
            or snapshot_manifest.get("requested_ref") != repository.get("commit")
            or snapshot_manifest.get("commit_sha") != repository.get("commit")
            or evaluator_materialization != expected_materialization):
        raise ComposerError("durable evaluator intake binding projection mismatch")
    try:
        return runctl._evaluator_intake_input_bindings(
            spec, spec_sha256, confirmed_sha256,
        )
    except (KeyError, TypeError) as exc:
        raise ComposerError("durable evaluator intake binding projection is incomplete") from exc


def _validate_evaluator_execution(run: Path, execution: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact historical evaluator/primary execution closure."""
    if set(execution) != {"evidence_class", "phases", "primary_receipt_sha256s", "aggregate_telemetry"}:
        raise ComposerError("evaluator execution member closure is not exact")
    evidence_class = execution.get("evidence_class")
    if evidence_class not in {"production", "test-only"}:
        raise ComposerError("invalid evaluator evidence class")
    phases = execution.get("phases")
    primary_hashes = execution.get("primary_receipt_sha256s")
    aggregate = execution.get("aggregate_telemetry")
    if (not isinstance(phases, list) or not phases
            or not isinstance(primary_hashes, dict)
            or set(primary_hashes) != {"intake", "resume", "finalize"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                   for value in primary_hashes.values())
            or not isinstance(aggregate, dict)
            or set(aggregate) != {"model_calls", "tool_calls", "model_requests_admitted",
                                  "input_tokens", "output_tokens", "wall_seconds"}):
        raise ComposerError("invalid evaluator execution closure")

    config = benchmark.load_frozen_config()
    runtime = config.get("runtime", {})
    track = benchmark._track(config, "as-shipped", "pangea")
    limits = {
        "model_calls": runtime.get("max_model_calls"),
        "tool_calls": track.get("max_tool_calls"),
        "input_tokens": runtime.get("context_window", 0) * runtime.get("max_model_calls", 0),
        "output_tokens": runtime.get("max_output_tokens", 0) * runtime.get("max_model_calls", 0),
        "wall_seconds": runtime.get("max_wall_clock_seconds"),
    }
    if any(type(value) is not int or value <= 0 for value in limits.values()):
        raise ComposerError("invalid frozen evaluator limits")

    phase_keys = {"phase", "role", "evidence_class", "session_id", "receipt_sha256", "command_sha256",
                  "input_bindings", "output_sha256", "telemetry"}
    telemetry_keys = {"model_calls", "tool_calls", "model_requests_admitted",
                      "model_calls_completed", "model_call_limit", "pre_request_budget_blocked",
                      "pre_request_budget_enforced", "injected_test_runner", "input_tokens",
                      "output_tokens", "max_step_input_tokens", "max_step_output_tokens",
                      "truncated", "duration_seconds"}
    totals = {key: 0 for key in ("model_calls", "tool_calls", "model_requests_admitted",
                                 "input_tokens", "output_tokens")}
    remaining = limits["model_calls"]
    sessions: set[str] = set()
    primary_rows: dict[str, dict[str, Any]] = {}
    sequence: list[tuple[str, str]] = []
    duration_total = 0.0
    digest_pattern = re.compile(r"[a-f0-9]{64}")
    for row in phases:
        if not isinstance(row, dict) or set(row) != phase_keys:
            raise ComposerError("evaluator phase member closure is not exact")
        phase, role, session = row.get("phase"), row.get("role"), row.get("session_id")
        if (not isinstance(phase, str) or not isinstance(role, str)
                or not isinstance(session, str) or not session or session in sessions):
            raise ComposerError("evaluator phase/role/session binding is invalid")
        sessions.add(session); sequence.append((phase, role))
        if row.get("evidence_class") != evidence_class:
            raise ComposerError("evaluator evidence class closure is inconsistent")
        if any(not isinstance(row.get(key), str) or not digest_pattern.fullmatch(row[key])
               for key in ("receipt_sha256", "command_sha256", "output_sha256")):
            raise ComposerError("evaluator phase digest binding is invalid")
        bindings = row.get("input_bindings")
        if (not isinstance(bindings, list) or not bindings
                or any(not isinstance(binding, dict) or set(binding) != {"name", "sha256"}
                       or not isinstance(binding["name"], str) or not binding["name"]
                       or not isinstance(binding["sha256"], str)
                       or not digest_pattern.fullmatch(binding["sha256"])
                       for binding in bindings)
                or len({binding["name"] for binding in bindings}) != len(bindings)):
            raise ComposerError("evaluator phase input binding is invalid")
        telemetry = row.get("telemetry")
        if not isinstance(telemetry, dict) or set(telemetry) != telemetry_keys:
            raise ComposerError("evaluator phase telemetry closure is not exact")
        for key in totals:
            if type(telemetry.get(key)) is not int or telemetry[key] < 0:
                raise ComposerError("evaluator aggregate telemetry is invalid")
            totals[key] += telemetry[key]
        expected_phase_limit = min(remaining, 4) if (phase, role) == ("intake", "primary") else 1
        if (type(telemetry["model_call_limit"]) is not int
                or telemetry["model_call_limit"] != expected_phase_limit
                or telemetry["model_call_limit"] < 1
                or telemetry["model_calls"] < 1
                or type(telemetry["model_calls_completed"]) is not int
                or telemetry["model_calls_completed"] != telemetry["model_calls"]
                or telemetry["model_requests_admitted"] < telemetry["model_calls_completed"]
                or telemetry["model_requests_admitted"] > expected_phase_limit
                or telemetry["pre_request_budget_blocked"] is not False):
            raise ComposerError("evaluator model budget signature mismatch")
        if ((phase, role) == ("intake", "primary")
                and (telemetry["model_calls"] > 4 or telemetry["tool_calls"] > 2)):
            raise ComposerError("evaluator intake one-shot budget exceeded")
        remaining -= telemetry["model_requests_admitted"]
        for key in ("pre_request_budget_blocked", "pre_request_budget_enforced",
                    "injected_test_runner", "truncated"):
            if type(telemetry.get(key)) is not bool:
                raise ComposerError("evaluator budget enforcement signature is invalid")
        if (telemetry["injected_test_runner"] != (evidence_class == "test-only")
                or telemetry["pre_request_budget_enforced"] != (evidence_class == "production")):
            raise ComposerError("evaluator evidence/runner relationship is invalid")
        if telemetry["truncated"]:
            raise ComposerError("evaluator execution was truncated")
        if (type(telemetry.get("max_step_input_tokens")) is not int
                or telemetry["max_step_input_tokens"] < 0
                or telemetry["max_step_input_tokens"] > runtime["context_window"]
                or type(telemetry.get("max_step_output_tokens")) is not int
                or telemetry["max_step_output_tokens"] < 0
                or telemetry["max_step_output_tokens"] > runtime["max_output_tokens"]):
            raise ComposerError("evaluator step token limit exceeded")
        duration = telemetry.get("duration_seconds")
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or duration < 0):
            raise ComposerError("evaluator phase duration is invalid")
        duration_total += duration
        if role == "primary":
            if phase not in {"intake", "resume", "finalize"} or phase in primary_rows:
                raise ComposerError("primary phase closure is not exact")
            primary_rows[phase] = row

    expected_primary = [("intake", "primary"), ("resume", "primary"), ("finalize", "primary")]
    if [item for item in sequence if item[1] == "primary"] != expected_primary:
        raise ComposerError("primary phase order is not exact")
    intake_index = sequence.index(("intake", "primary"))
    resume_index = sequence.index(("resume", "primary"))
    audit_index = sequence.index(("report-auditor", "auditor")) if sequence.count(("report-auditor", "auditor")) == 1 else -1
    finalize_index = sequence.index(("finalize", "primary"))
    if (intake_index != 0 or not (intake_index < resume_index < audit_index < finalize_index)
            or finalize_index != len(sequence) - 1
            or any(pair not in {("intake", "primary"), ("analysis-worker", "analysis-worker"),
                                ("auditor", "auditor"), ("resume", "primary"),
                                ("report-auditor", "auditor"), ("finalize", "primary")}
                   for pair in sequence)
            or not any(pair == ("analysis-worker", "analysis-worker") for pair in sequence[:resume_index])
            or not any(pair == ("auditor", "auditor") for pair in sequence[:resume_index])):
        raise ComposerError("evaluator phase role/order closure is invalid")

    for key, maximum in limits.items():
        value = aggregate.get(key)
        if key == "wall_seconds":
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < duration_total or value > maximum):
                raise ComposerError("evaluator wall-clock aggregate is invalid")
        elif type(value) is not int or value != totals[key] or value > maximum:
            raise ComposerError("evaluator aggregate arithmetic mismatch")

    intake_bindings = _recomputed_evaluator_intake_bindings(run)
    directory = run / "internal/primary-receipts"
    try:
        path_closed = (run.resolve(strict=True) == run
                       and (run / "internal").resolve(strict=True) == run / "internal"
                       and directory.resolve(strict=True) == directory)
    except OSError:
        path_closed = False
    if not path_closed or directory.is_symlink() or not directory.is_dir():
        raise ComposerError("primary receipt directory is invalid")
    try:
        directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                               | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ComposerError("primary receipt directory cannot be opened exactly") from exc
    try:
        before_directory = _stat_fingerprint(os.fstat(directory_fd))
        members = os.listdir(directory_fd)
        if set(members) != {"intake.json", "resume.json", "finalize.json"} or len(members) != 3:
            raise ComposerError("primary receipt member closure is not exact")
        candidate_binding: tuple[Any, Any, Any] | None = None
        for phase in ("intake", "resume", "finalize"):
            try:
                descriptor = os.open(phase + ".json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                     dir_fd=directory_fd)
            except OSError as exc:
                raise ComposerError("primary receipt is not a stable regular file") from exc
            try:
                before = os.fstat(descriptor); chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk: break
                    chunks.append(chunk)
                after = os.fstat(descriptor); encoded = b"".join(chunks)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                        or before.st_mode & 0o222 or before.st_nlink != 1
                        or _stat_fingerprint(before) != _stat_fingerprint(after)):
                    raise ComposerError("primary receipt changed while being read")
            finally:
                os.close(descriptor)
            digest = sha256(encoded).hexdigest()
            if primary_hashes.get(phase) != digest:
                raise ComposerError("primary receipt hash map mismatch")
            try:
                value = json.loads(encoded.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ComposerError("invalid durable primary receipt JSON") from exc
            top_keys = {"artifact_type", "schema_version", "captured_by", "evidence_class", "phase", "candidate",
                        "track", "case_id", "command_sha256", "exit_code", "duration_seconds",
                        "preflight_sha256", "policy_sha256", "phase_prompt_sha256",
                        "model_budget_hook_sha256", "input_bindings", "output_sha256",
                        "telemetry", "passed", "failures"}
            primary_telemetry_keys = {"model_calls", "tool_calls", "input_tokens", "output_tokens",
                                      "max_step_input_tokens", "max_step_output_tokens", "finish_reasons",
                                      "final_finish_reason", "finish_reason_observed", "session_ids", "truncated",
                                      "model_call_limit", "model_calls_completed", "model_requests_admitted",
                                      "pre_request_budget_blocked", "pre_request_budget_enforced",
                                      "injected_test_runner", "tool_input_policy_violation_summary"}
            if (not isinstance(value, dict) or set(value) != top_keys
                    or value.get("artifact_type") != "primary_run_receipt"
                    or value.get("schema_version") != "1.1" or value.get("captured_by") != "evaluator"
                    or value.get("evidence_class") != evidence_class
                    or value.get("phase") != phase or not isinstance(value.get("telemetry"), dict)
                    or set(value["telemetry"]) != primary_telemetry_keys
                    or not isinstance(value.get("case_id"), str) or not value["case_id"]
                    or type(value.get("exit_code")) is not int or value["exit_code"] != 0
                    or isinstance(value.get("duration_seconds"), bool)
                    or not isinstance(value.get("duration_seconds"), (int, float))
                    or not math.isfinite(value["duration_seconds"]) or value["duration_seconds"] < 0
                    or any(not isinstance(value.get(key), str)
                           or not digest_pattern.fullmatch(value[key])
                           for key in ("command_sha256", "preflight_sha256", "policy_sha256",
                                       "phase_prompt_sha256", "model_budget_hook_sha256",
                                       "output_sha256"))):
                raise ComposerError("durable primary receipt closure is invalid")
            binding = (value.get("candidate"), value.get("track"), value.get("case_id"))
            candidate_binding = binding if candidate_binding is None else candidate_binding
            if binding != candidate_binding or binding[0:2] != ("pangea", "as-shipped"):
                raise ComposerError("primary receipt candidate binding mismatch")
            phase_row = primary_rows[phase]
            phase_telemetry = phase_row["telemetry"]
            expected_bindings = [{"name": "phase_prompt",
                                  "sha256": value.get("phase_prompt_sha256")}]
            if phase == "intake":
                expected_bindings.extend(intake_bindings)
            if (value.get("command_sha256") != phase_row["command_sha256"]
                    or value.get("output_sha256") != phase_row["output_sha256"]
                    or value["telemetry"].get("session_ids") != [phase_row["session_id"]]
                    or any(value["telemetry"].get(key) != phase_telemetry[key]
                           for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens",
                                       "max_step_input_tokens", "max_step_output_tokens", "truncated",
                                       "model_call_limit", "model_calls_completed", "model_requests_admitted",
                                       "pre_request_budget_blocked", "pre_request_budget_enforced",
                                       "injected_test_runner"))
                    or not isinstance(value.get("phase_prompt_sha256"), str)
                    or not digest_pattern.fullmatch(value["phase_prompt_sha256"])
                    or value.get("input_bindings") != expected_bindings
                    or phase_row["input_bindings"] != expected_bindings):
                raise ComposerError("primary receipt phase binding mismatch")
            if (value["telemetry"].get("final_finish_reason") != "stop"
                    or value["telemetry"].get("finish_reason_observed") is not True
                    or value["telemetry"].get("truncated") is not False):
                raise ComposerError("primary receipt successful output telemetry is invalid")
            if (phase == "intake") != (value.get("passed") is False
                                        and value.get("failures") == ["external_role_execution_required"]):
                raise ComposerError("primary receipt result binding mismatch")
            if phase != "intake" and (value.get("passed") is not True or value.get("failures") != []):
                raise ComposerError("primary receipt result binding mismatch")
        if (before_directory != _stat_fingerprint(os.fstat(directory_fd))
                or set(os.listdir(directory_fd)) != set(members)):
            raise ComposerError("primary receipt directory changed while being read")
    finally:
        os.close(directory_fd)
    return dict(execution)


def _authoritative_primary_bindings(execution: Mapping[str, Any]) -> dict[str, str]:
    phases = execution["phases"]
    finalize = next(row for row in phases if row["phase"] == "finalize" and row["role"] == "primary")
    primary_hashes = execution["primary_receipt_sha256s"]
    return {
        "primary_intake_sha256": primary_hashes["intake"],
        "primary_finalize_sha256": primary_hashes["finalize"],
        "final_output_sha256": finalize["output_sha256"],
    }


def _validate_public_bundle_binding(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = {"artifact_type", "schema_version", "managed_root",
            "immutable_managed_subroots", "entries", "files"}
    entries = value.get("entries")
    files = value.get("files")
    if (set(value) != keys
            or value.get("artifact_type") != "immutable_public_bundle_binding"
            or value.get("schema_version") != "1.0"
            or value.get("managed_root") != "pangea-data"
            or value.get("immutable_managed_subroots") != ["library", "registry", "repositories"]
            or not isinstance(entries, list) or entries != sorted(entries)
            or len(set(entries)) != len(entries)
            or any(not isinstance(path, str) or not path or Path(path).is_absolute()
                   or ".." in Path(path).parts or Path(path).as_posix() != path for path in entries)
            or not isinstance(files, list)
            or any(not isinstance(row, dict) or set(row) != {"path", "sha256"}
                   or row.get("path") not in entries
                   or not isinstance(row.get("sha256"), str)
                   or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) for row in files)
            or [row["path"] for row in files] != sorted(row["path"] for row in files)
            or len({row["path"] for row in files}) != len(files)):
        raise ComposerError("immutable public bundle closure is invalid")
    return dict(value)


def _validate_composed_receipt_shape(receipt: Mapping[str, Any], *,
                                     require_execution: bool,
                                     require_public_bundle: bool) -> None:
    keys = {
        "artifact_type", "schema_version", "run_id", "primary_intake_sha256",
        "primary_finalize_sha256", "leaf_receipt_sha256s", "attestation_file_sha256s",
        "fragment_file_sha256s", "telemetry_file_sha256s",
        "semantic_assessment_file_sha256s", "merged_sha256", "analysis_file_sha256",
        "report_file_sha256", "coverage_judge_file_sha256", "final_output_sha256",
        "run_file_bindings", "sha256",
    }
    if require_execution:
        keys |= {"evidence_class", "evaluator_execution", "final_audit_attestation_file_sha256s"}
    if require_public_bundle:
        keys |= {"public_bundle_binding", "formal_outputs"}
    elif "formal_outputs" in receipt:
        keys.add("formal_outputs")
    if set(receipt) != keys:
        raise ComposerError("composed receipt member closure is not exact")
    digest = re.compile(r"[a-f0-9]{64}")
    scalar_hashes = {"primary_intake_sha256", "primary_finalize_sha256", "merged_sha256",
                     "analysis_file_sha256", "report_file_sha256", "coverage_judge_file_sha256",
                     "final_output_sha256", "sha256"}
    list_hashes = {"leaf_receipt_sha256s", "attestation_file_sha256s",
                   "fragment_file_sha256s", "telemetry_file_sha256s",
                   "semantic_assessment_file_sha256s"}
    if require_execution:
        list_hashes.add("final_audit_attestation_file_sha256s")
    if (receipt.get("artifact_type") != "composed_run_receipt"
            or receipt.get("schema_version") != "1.0"
            or not isinstance(receipt.get("run_id"), str) or not receipt["run_id"]
            or any(not isinstance(receipt.get(key), str) or not digest.fullmatch(receipt[key])
                   for key in scalar_hashes)
            or any(not isinstance(receipt.get(key), list)
                   or receipt[key] != sorted(receipt[key])
                   or any(not isinstance(item, str) or not digest.fullmatch(item)
                          for item in receipt[key]) for key in list_hashes)
            or not isinstance(receipt.get("run_file_bindings"), list)
            or any(not isinstance(row, dict) or set(row) != {"path", "sha256"}
                   or not isinstance(row.get("path"), str)
                   or not isinstance(row.get("sha256"), str)
                   or not digest.fullmatch(row["sha256"]) for row in receipt["run_file_bindings"])):
        raise ComposerError("composed receipt schema validation failed")
    if require_execution:
        if not isinstance(receipt.get("evaluator_execution"), Mapping):
            raise ComposerError("composed evaluator execution is missing")
    if require_public_bundle:
        if not isinstance(receipt.get("public_bundle_binding"), Mapping):
            raise ComposerError("composed immutable public bundle binding is missing")
        _validate_public_bundle_binding(receipt["public_bundle_binding"])
        if not isinstance(receipt.get("formal_outputs"), list):
            raise ComposerError("composed formal output closure is missing")


@dataclass(frozen=True)
class ComposerCallbacks:
    """Evaluator-owned hooks; no callback is a product runtime role."""
    primary_intake: Callable[[], Mapping[str, Any]]
    primary_finalize: Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]
    active_runs: Callable[[Path], list[Path]] | None = None
    build_denominator: Callable[[Path, str], Mapping[str, Any]] = analysis_pipeline.build_denominator
    issue_context: Callable[[Path, str], Mapping[str, Any]] = analysis_pipeline.issue_context
    execute_role: Callable[[str, Mapping[str, Any]], benchmark.TrustedRoleExecution] = benchmark.execute_isolated_role
    write_worker: Callable[[Path, Path, benchmark.TrustedRoleExecution], Path] = benchmark.write_isolated_worker_fragment
    apply_fragment: Callable[[Path, str, Path], Mapping[str, Any]] = analysis_pipeline.apply_fragment
    write_telemetry: Callable[[Path, Path, Path, benchmark.TrustedRoleExecution], Path] = benchmark.write_native_runner_telemetry
    write_assessment: Callable[[Path, dict[str, Any], list[dict[str, Any]], benchmark.TrustedRoleExecution], Path] = benchmark.write_native_semantic_assessment
    write_assessment_batch: Callable[[Path, dict[str, Any], benchmark.TrustedRoleExecution], list[Path]] = benchmark.write_native_semantic_assessment_batch
    validate: Callable[[Path, str], Mapping[str, Any]] = analysis_pipeline.validate_run_for_judge
    coverage_judge: Callable[[Path], Mapping[str, Any]] | None = None
    verify_attestation: Callable[[Path, str], str] = _verify_attestation
    execution_closure: Callable[[], Mapping[str, Any]] | None = None
    public_bundle_closure: Callable[[], Mapping[str, Any]] | None = None


def compose_complete_run(root: Path, callbacks: ComposerCallbacks) -> dict[str, Any]:
    """Run the closed evaluator composition and return its immutable receipt.

    Re-entry is deterministic: writers accept only byte-identical existing
    artifacts, and every phase re-derives exact assignment/claim closures.
    """
    root = Path(root).resolve()
    existing = _existing_composed_receipt(
        root, callbacks.active_runs, callbacks.verify_attestation, callbacks.execution_closure,
        callbacks.public_bundle_closure,
    )
    if existing is not None:
        return existing
    intake = dict(callbacks.primary_intake())
    _primary_blocked(intake)
    run = _single_active_run(root, callbacks.active_runs)
    run_id = run.name
    try:
        callbacks.build_denominator(root, run_id)
        callbacks.issue_context(root, run_id)
    except analysis_pipeline.PipelineError as exc:
        raise ComposerError("denominator/context phase failed") from exc
    run_id, assignments, contexts = _assignments_and_contexts(run)

    leaf_hashes: list[str] = []; executions: list[tuple[str,str]] = []
    for assignment in assignments:
        fid = assignment["fragment_id"]; context = contexts[fid]
        managed = run / "internal/fragments" / (fid + ".json")
        telemetry_path = run / "internal/telemetry" / (fid + ".json")
        if managed.exists() or telemetry_path.exists():
            if not (managed.is_file() and telemetry_path.is_file()):
                raise ComposerError("worker recovery checkpoint is incomplete: " + fid)
            telemetry = _json(telemetry_path)
            receipt_hash = telemetry.get("execution_receipt_sha256")
            if not isinstance(receipt_hash, str):
                raise ComposerError("worker recovery receipt binding missing: " + fid)
            attestation_path = run / "internal/execution-receipts" / (receipt_hash + ".json")
            callbacks.verify_attestation(attestation_path, "analysis-worker")
            raw_receipt = _json(attestation_path).get("receipt")
            if not isinstance(raw_receipt, dict):
                raise ComposerError("worker recovery receipt invalid: " + fid)
            leaf_hashes.append(_hash(raw_receipt)); executions.append(("analysis-worker", receipt_hash))
            continue
        try:
            context_value=_json(context); compact=context_value.get("payload",{}).get("candidate",{}).get("compact_context")
            if not isinstance(compact,dict): raise ComposerError("compact analysis context is missing")
            execution = callbacks.execute_role("analysis-worker", {"COMPACT_CONTEXT.json": compact})
            imported = callbacks.write_worker(run, context, execution)
        except Exception as exc:
            raise ComposerError("analysis-worker execution failed: " + fid) from exc
        try:
            callbacks.apply_fragment(root, run_id, imported)
        except Exception as exc:
            raise ComposerError("worker fragment transaction failed: " + fid) from exc
        managed = run / "internal/fragments" / (fid + ".json")
        try:
            callbacks.write_telemetry(run, managed, context, execution)
        except Exception as exc:
            raise ComposerError("worker telemetry transaction failed: " + fid) from exc
        leaf_hashes.append(_hash(dict(execution.receipt)))
        executions.append(("analysis-worker", _hash(dict(execution.receipt))))

    _compact_adapter_closure(run,assignments,contexts,callbacks.verify_attestation)
    # A second exact read detects both incomplete transactions and unexpected
    # assignments before claims are handed to independent auditors.
    _, assignments, _ = _assignments_and_contexts(run)
    fragments: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        fid = assignment["fragment_id"]
        fragments[fid] = _payload(run / "internal/fragments" / (fid + ".json"), "fragment_artifact", run_id)
    merged = fragment_runtime.merge_fragments([fragments[row["fragment_id"]] for row in assignments])
    expected_claims: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for fid, fragment in fragments.items():
        for family in fragment_runtime.CONTRIBUTION_FAMILIES:
            for claim in fragment["contributions"][family]:
                claim_id = claim["contribution_id"]
                if claim_id in expected_claims:
                    raise ComposerError("duplicate fragment claim: " + claim_id)
                expected_claims[claim_id] = (claim, fragment["facts"])
        for claim in fragment["risk_cards"]:
            claim_id = claim["risk_id"]
            if claim_id in expected_claims:
                raise ComposerError("duplicate fragment claim: " + claim_id)
            expected_claims[claim_id] = (claim, fragment["facts"])
    merged_ids = {claim.get("contribution_id", claim.get("risk_id")) for family in fragment_runtime.CONTRIBUTION_FAMILIES for claim in merged["contributions"][family]}
    merged_ids.update(claim["risk_id"] for claim in merged["risk_cards"])
    if not expected_claims or set(expected_claims) != merged_ids:
        raise ComposerError("merged claim closure is incomplete")
    claim_ids=sorted(expected_claims)
    for start in range(0,len(claim_ids),100):
        entries=[]
        for ordinal,claim_id in enumerate(claim_ids[start:start+100]):
            claim,facts=expected_claims[claim_id]; keys={tuple(key) for key in claim["fact_keys"]}
            selected=[fact for fact in facts if (fact.get("obligation_id"),fact.get("inventory_id"),fact.get("line_start"),fact.get("line_count")) in keys]
            entries.append({"ordinal":ordinal,"claim":claim,"facts":selected})
        batch={"v":1,"claims":entries}
        existing=[run/"internal/semantic-assessments"/(entry["claim"].get("contribution_id",entry["claim"].get("risk_id"))+".json") for entry in entries]
        if any(path.exists() for path in existing):
            if not all(path.exists() for path in existing): raise ComposerError("auditor batch recovery checkpoint is incomplete")
            receipt_hashes={_json(path).get("auditor_telemetry",{}).get("execution_receipt_sha256") for path in existing}
            if len(receipt_hashes)!=1 or not isinstance(next(iter(receipt_hashes)),str): raise ComposerError("auditor batch recovery receipt binding missing")
            receipt_hash=next(iter(receipt_hashes));attestation_path=run/"internal/execution-receipts"/(receipt_hash+".json")
            callbacks.verify_attestation(attestation_path,"auditor");raw_receipt=_json(attestation_path).get("receipt")
            if not isinstance(raw_receipt,dict): raise ComposerError("auditor batch recovery receipt invalid")
            leaf_hashes.append(_hash(raw_receipt));executions.append(("auditor",receipt_hash));continue
        try:
            execution=callbacks.execute_role("auditor",{"SEMANTIC_BATCH.json":batch})
            callbacks.write_assessment_batch(run,batch,execution)
        except Exception as exc: raise ComposerError("auditor batch execution failed") from exc
        leaf_hashes.append(_hash(dict(execution.receipt)));executions.append(("auditor",_hash(dict(execution.receipt))))

    assessment_hashes = _semantic_closure(run, expected_claims)

    try:
        validation = dict(callbacks.validate(root, run_id))
    except Exception as exc:
        raise ComposerError("Run replay validation failed") from exc
    if validation.get("status") != "verified":
        raise ComposerError("Run replay validation did not pass")
    if callbacks.coverage_judge is None:
        raise ComposerError("Coverage Judge callback is required")
    callbacks.coverage_judge(run)
    judge, fixed_hashes = _fixed_judge_closure(run, run_id)

    try:
        finalized = dict(callbacks.primary_finalize(run, {"validation": validation, "coverage_judge": judge, "merged_sha256": merged["sha256"]}))
    except Exception as exc:
        raise ComposerError("primary finalization failed") from exc
    required = {"analysis_bound", "report_bound", "judge_bound"}
    if any(finalized.get(key) is not True for key in required):
        raise ComposerError("primary finalization lacks fixed analysis/report/Judge bindings")
    final_text = finalized.get("report") or finalized.get("final_text")
    if not isinstance(final_text, str) or not final_text.strip():
        raise ComposerError("primary finalization did not produce final output")
    bindings = finalized.get("bindings")
    if (not isinstance(bindings, Mapping) or set(bindings) != {"analysis", "report", "coverage_judge"}
            or any(not isinstance(value, str) or len(value) != 64 for value in bindings.values())
            or dict(bindings) != fixed_hashes):
        raise ComposerError("primary finalization has stale or incomplete bindings")
    # Bindings returned by the primary are advisory.  Re-read and recompute
    # every fixed artifact after the process exits to close the TOCTOU window.
    post_judge, post_hashes = _fixed_judge_closure(run, run_id)
    if post_judge != judge or post_hashes != fixed_hashes:
        raise ComposerError("fixed artifacts changed during primary finalization")
    fragment_hashes, telemetry_hashes, attestation_hashes = _exact_leaf_files(run, assignments, executions, callbacks.verify_attestation)
    receipt = {"artifact_type": "composed_run_receipt", "schema_version": "1.0", "run_id": run_id,
               "primary_intake_sha256": _hash(intake), "primary_finalize_sha256": _hash(finalized),
               "leaf_receipt_sha256s": sorted(leaf_hashes), "attestation_file_sha256s": sorted(attestation_hashes),
               "fragment_file_sha256s": sorted(fragment_hashes), "telemetry_file_sha256s": sorted(telemetry_hashes),
               "semantic_assessment_file_sha256s": assessment_hashes, "merged_sha256": merged["sha256"],
               "analysis_file_sha256": fixed_hashes["analysis"], "report_file_sha256": fixed_hashes["report"],
               "coverage_judge_file_sha256": fixed_hashes["coverage_judge"],
               "final_output_sha256": sha256(final_text.encode()).hexdigest()}
    evaluator_execution = (callbacks.execution_closure() if callbacks.execution_closure is not None
                           else finalized.get("evaluator_execution"))
    if evaluator_execution is not None:
        if not isinstance(evaluator_execution, Mapping):
            raise ComposerError("invalid evaluator execution closure")
        evaluator_execution = _validate_evaluator_execution(run, evaluator_execution)
        receipt["evidence_class"] = evaluator_execution["evidence_class"]
        _validate_leaf_evidence_class(run, evaluator_execution["evidence_class"])
        receipt["evaluator_execution"] = evaluator_execution
        receipt.update(_authoritative_primary_bindings(evaluator_execution))
        if receipt["final_output_sha256"] != sha256(final_text.encode()).hexdigest():
            raise ComposerError("primary final output changed before composed sealing")
        receipt["final_audit_attestation_file_sha256s"] = _final_audit_closure(
            run, evaluator_execution, callbacks.verify_attestation,
        )
    formal_outputs = finalized.get("formal_outputs")
    if formal_outputs is not None:
        receipt["formal_outputs"] = _verify_formal_output_closure(root, formal_outputs)
    receipt["run_file_bindings"] = _run_file_bindings(run)
    final_file_map = {row["path"]: row["sha256"] for row in receipt["run_file_bindings"]}
    if (final_file_map.get("internal/analysis-model.json") != fixed_hashes["analysis"]
            or final_file_map.get("internal/report-model.json") != fixed_hashes["report"]
            or final_file_map.get("internal/coverage-judge.json") != fixed_hashes["coverage_judge"]):
        raise ComposerError("fixed artifacts changed during final receipt sealing")
    # The closures above can be non-trivial.  Refresh the end-to-end clock
    # only after all of them have completed, then re-bind the final auditor,
    # before making the composed receipt durable.
    if callbacks.execution_closure is not None:
        latest_execution = callbacks.execution_closure()
        if not isinstance(latest_execution, Mapping):
            raise ComposerError("invalid final evaluator execution closure")
        latest_execution = _validate_evaluator_execution(run, latest_execution)
        _validate_leaf_evidence_class(run, latest_execution["evidence_class"])
        receipt["evaluator_execution"] = latest_execution
        receipt.update(_authoritative_primary_bindings(latest_execution))
        if receipt["final_output_sha256"] != sha256(final_text.encode()).hexdigest():
            raise ComposerError("primary final output changed before final sealing")
        receipt["final_audit_attestation_file_sha256s"] = _final_audit_closure(
            run, latest_execution, callbacks.verify_attestation,
        )
    if callbacks.public_bundle_closure is not None:
        public_bundle = callbacks.public_bundle_closure()
        if not isinstance(public_bundle, Mapping):
            raise ComposerError("invalid immutable public bundle closure")
        receipt["public_bundle_binding"] = _validate_public_bundle_binding(public_bundle)
    receipt["sha256"] = _hash(receipt)
    _validate_composed_receipt_shape(
        receipt, require_execution=callbacks.execution_closure is not None,
        require_public_bundle=callbacks.public_bundle_closure is not None,
    )
    _write_exact(run / "internal/composed-receipt.json", receipt)
    return receipt


# Short public spelling for evaluators that expose the runner as ``compose``.
compose = compose_complete_run
