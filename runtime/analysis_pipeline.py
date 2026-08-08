"""Deterministic R2 analysis pipeline artifacts.

This module deliberately does not invoke a model.  It turns a confirmed,
commit-bound snapshot into an independently derived denominator and immutable
worker packs.  Model output is accepted only through ``apply_fragment``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import fcntl
import copy
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from runtime import data_runtime, repository_runtime
from runtime import source_inventory, obligation_ledger, context_budget, fragment_runtime, compact_protocol

VERSION = "2.0"
_REPO = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

class PipelineError(RuntimeError):
    pass

def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

def _root(root: Path, run_id: str) -> Path:
    try:
        run, _ = data_runtime._load_run(root, run_id)
    except data_runtime.DataRuntimeError as exc:
        raise PipelineError(str(exc)) from exc
    if run.is_symlink():
        raise PipelineError("run may not be a symlink")
    return run.resolve()

def _parent_fd(path: Path, create: bool=False) -> int:
    absolute=path.absolute()
    fd=os.open("/",os.O_RDONLY|getattr(os,"O_DIRECTORY",0))
    try:
        for component in absolute.parent.parts[1:]:
            if create:
                try: os.mkdir(component,0o700,dir_fd=fd)
                except FileExistsError: pass
            nxt=os.open(component,os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0),dir_fd=fd)
            os.close(fd); fd=nxt
        return fd
    except OSError as exc:
        os.close(fd); raise PipelineError(f"unsafe artifact ancestor: {path}") from exc

_ENVELOPE_KEYS = {"artifact_type", "schema_version", "run_id", "contract_sha256", "payload", "payload_sha256"}

def _read_json(path: Path) -> dict[str, Any]:
    try:
        parent=_parent_fd(path); fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),dir_fd=parent); os.close(parent)
        if not stat.S_ISREG(os.fstat(fd).st_mode): raise PipelineError("managed artifact is not regular")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"invalid managed JSON: {path}") from exc
    if not isinstance(value, dict): raise PipelineError("managed JSON root must be object")
    return value

def _read(path: Path) -> dict[str, Any]:
    """Read a managed R2 artifact; never accept a legacy/plain JSON object."""
    value = _read_json(path)
    _validate_artifact(value)
    return value

def _validate_artifact(value: dict[str, Any]) -> None:
    if (not isinstance(value, dict) or set(value) != _ENVELOPE_KEYS
            or value.get("schema_version") != VERSION
            or value.get("payload_sha256") != _digest(value.get("payload"))):
        raise PipelineError("invalid pipeline artifact envelope or payload hash")
    from runtime.runctl import validate
    try:
        validate(value, "pipeline-artifact.schema.json")
    except Exception as exc:
        raise PipelineError("pipeline artifact schema violation") from exc
    kind, payload = value["artifact_type"], value["payload"]
    binding_keys={"repository","commit","snapshot_sha256","snapshot_content_sha256","snapshot_id","scope","inventory_sha256","ledger_sha256"}
    if kind in {"source_bindings","inventory_index"}:
        if (set(payload) != {"repositories"} or any(set(x) != binding_keys for x in payload["repositories"])
                or len({x["repository"] for x in payload["repositories"]}) != len(payload["repositories"])):
            raise PipelineError("invalid strict repository index payload")
    elif kind == "obligation_index":
        allowed=binding_keys|{"obligation_count","status_counts"}
        if (set(payload) != {"repositories"} or any(set(x)-allowed or not binding_keys|{"obligation_count"} <= set(x) for x in payload["repositories"])
                or len({x["repository"] for x in payload["repositories"]}) != len(payload["repositories"])):
            raise PipelineError("invalid strict obligation index payload")
    elif kind == "assignment_index":
        base={"fragment_id","repository","obligation_ids","worker_id","context_pack_sha256","candidate_sha256","ledger_sha256","status","skill_receipt_ids","overhead_measurement_status"}
        if (set(payload) != {"assignments"}
                or any(set(x) != (base|{"fragment_sha256","applied_ledger_sha256"} if x.get("status")=="applied" else base)
                       for x in payload["assignments"])
                or len({x["fragment_id"] for x in payload["assignments"]}) != len(payload["assignments"])):
            raise PipelineError("invalid strict assignment index payload")
    if kind == "inventory_artifact":
        validate(payload, "source-inventory.schema.json")
    elif kind == "obligation_ledger_artifact":
        validate(payload, "obligation-ledger.schema.json")
    elif kind == "context_pack_artifact":
        if set(payload) != {"candidate", "candidate_sha256"} or payload["candidate_sha256"] != _digest(payload["candidate"]):
            raise PipelineError("invalid context artifact payload")
        validate(payload["candidate"].get("context_pack"), "context-pack.schema.json")
    elif kind == "fragment_artifact":
        validate(payload, "analysis-fragment.schema.json")
    elif kind == "publication_state":
        denominator={"status","contract_sha256","artifacts"}; context={"status","worker_id","previous_assignment_sha256","assignment_sha256","assignments","contexts","capacity_plan"}
        if set(payload) not in (denominator,context): raise PipelineError("invalid strict publication state")
        if set(payload)==denominator and len({x["path"] for x in payload["artifacts"]})!=len(payload["artifacts"]):
            raise PipelineError("duplicate denominator publication path")
        if set(payload)==context:
            assignments=[x["fragment_id"] for x in payload["assignments"]]; contexts=[x["fragment_id"] for x in payload["contexts"]]
            if len(set(assignments))!=len(assignments) or len(set(contexts))!=len(contexts) or set(assignments)!=set(contexts):
                raise PipelineError("context publication reference set drift")
            _validate_capacity_projection(payload["capacity_plan"])


def _validate_capacity_projection(value: Any) -> None:
    top={"version","repositories","analysis_worker_calls","semantic_auditor_calls","fixed_model_call_caps",
         "worst_model_calls","max_model_calls","native_output_byte_limit","input_byte_limit",
         "maximum_compact_input_bytes","maximum_native_output_bytes"}
    row_keys={"version","repository","commit","ordinal_map_sha256","inventory_items","obligations",
              "analysis_worker_calls","analysis_worker_call_limit","semantic_auditor_calls",
              "semantic_auditor_call_limit","fixed_model_call_caps","worst_model_calls","max_model_calls",
              "native_output_byte_limit","input_byte_limit","maximum_compact_input_bytes",
              "maximum_native_output_bytes"}
    fixed=dict(compact_protocol.FIXED_MODEL_CALL_CAPS)
    if (not isinstance(value,dict) or set(value)!=top or value.get("version")!=compact_protocol.VERSION
            or value.get("fixed_model_call_caps")!=fixed or value.get("max_model_calls")!=compact_protocol.MAX_MODEL_CALLS
            or value.get("native_output_byte_limit")!=compact_protocol.NATIVE_OUTPUT_BYTE_LIMIT
            or value.get("input_byte_limit")!=compact_protocol.INPUT_BYTE_LIMIT):
        raise PipelineError("compact capacity projection closure is invalid")
    repositories=value.get("repositories")
    if not isinstance(repositories,dict) or not repositories or list(repositories)!=sorted(repositories):
        raise PipelineError("compact capacity repository closure is invalid")
    for repository,row in repositories.items():
        numeric=("inventory_items","obligations","analysis_worker_calls","analysis_worker_call_limit",
                 "semantic_auditor_calls","semantic_auditor_call_limit","worst_model_calls","max_model_calls",
                 "native_output_byte_limit","input_byte_limit","maximum_compact_input_bytes",
                 "maximum_native_output_bytes")
        if (not isinstance(row,dict) or set(row)!=row_keys or row.get("version")!=compact_protocol.VERSION
                or row.get("repository")!=repository or not re.fullmatch(r"[a-f0-9]{40}",str(row.get("commit","")))
                or not re.fullmatch(r"[a-f0-9]{64}",str(row.get("ordinal_map_sha256","")))
                or any(type(row.get(name)) is not int or row[name]<1 for name in numeric)
                or row.get("analysis_worker_call_limit")!=compact_protocol.ANALYSIS_WORKER_CALL_LIMIT
                or row.get("semantic_auditor_call_limit")!=compact_protocol.SEMANTIC_AUDITOR_CALL_LIMIT
                or row.get("fixed_model_call_caps")!=fixed or row.get("max_model_calls")!=compact_protocol.MAX_MODEL_CALLS
                or row.get("native_output_byte_limit")!=compact_protocol.NATIVE_OUTPUT_BYTE_LIMIT
                or row.get("input_byte_limit")!=compact_protocol.INPUT_BYTE_LIMIT):
            raise PipelineError("compact capacity repository binding is invalid")
    calls=sum(row["analysis_worker_calls"] for row in repositories.values())
    auditors=(calls*compact_protocol.WORKER_CLAIM_LIMIT+compact_protocol.AUDITOR_CLAIM_LIMIT-1)//compact_protocol.AUDITOR_CLAIM_LIMIT
    worst=sum(fixed.values())+calls+auditors
    if (value.get("analysis_worker_calls")!=calls or value.get("semantic_auditor_calls")!=auditors
            or value.get("worst_model_calls")!=worst
            or value.get("maximum_compact_input_bytes")!=max(row["maximum_compact_input_bytes"] for row in repositories.values())
            or value.get("maximum_native_output_bytes")!=max(row["maximum_native_output_bytes"] for row in repositories.values())):
        raise PipelineError("compact aggregate capacity relationship is invalid")

def _write(path: Path, value: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise PipelineError("refusing symlink artifact")
    # All pipeline artifacts share a closed envelope; content is additionally
    # validated by the R1 artifact validators before publication.
    _validate_artifact(value)
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    parentfd=_parent_fd(path,create=True); tmp = "." + path.name + ".tmp-" + hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400,dir_fd=parentfd)
    try:
        view=memoryview(content)
        while view: view=view[os.write(fd,view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp,path.name,src_dir_fd=parentfd,dst_dir_fd=parentfd); os.fsync(parentfd); os.close(parentfd)
    # The exclusive temporary inode was created 0400; later transactions
    # replace it rather than reopening it for in-place mutation.

@contextmanager
def _run_lock(run: Path):
    """Serialize ledger/index publication across independent worker processes."""
    lock = run / "internal/.pipeline.lock"
    parentfd=_parent_fd(lock,create=True)
    fd = os.open(lock.name, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600,dir_fd=parentfd); os.close(parentfd)
    with os.fdopen(fd, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

def _fault(point: str) -> None:
    if os.environ.get("PANGEA_PIPELINE_FAULT") == point:
        raise PipelineError("injected transaction interruption: " + point)

def _read_fragment_import(path: Path, run: Path) -> dict[str, Any]:
    """Import only a regular worker output explicitly placed under run/tmp.

    This avoids treating an arbitrary host path as trusted input and makes the
    import boundary visible to operators and tests.
    """
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to((run / "tmp").resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise PipelineError("fragment must be imported from this run's tmp directory") from exc
    return _read_json(path)

def _contract(run: Path) -> dict[str, Any]:
    contract = _read_json(run / "internal/task-contract.json")
    repos = contract.get("repositories")
    commits = contract.get("repository_commits")
    scopes = contract.get("source_scopes")
    if (contract.get("mode") != "module_analysis" or not isinstance(repos, list) or not repos
        or not isinstance(commits, dict) or set(commits) != set(repos)
        or not isinstance(scopes, dict) or set(scopes) != set(repos)):
        raise PipelineError("R2 module pipeline requires complete commit-bound source_scopes")
    for repo in repos:
        scope = scopes[repo]
        if not _REPO.fullmatch(repo) or not isinstance(commits[repo], str) or not re.fullmatch(r"[0-9a-f]{40}", commits[repo]):
            raise PipelineError("invalid repository commit binding")
        if not isinstance(scope, list) or not scope or len(scope) != len(set(scope)):
            raise PipelineError("each repository needs nonempty confirmed source scope")
    # The scope is user-confirmed input, not an inference from the snapshot.
    # Bind its canonical content explicitly so a later contract edit cannot
    # silently widen an already-issued denominator.
    scope_hash = contract.get("source_scopes_sha256")
    if scope_hash != _digest({repo: scopes[repo] for repo in sorted(repos)}):
        raise PipelineError("source scope confirmation hash mismatch")
    record = _read_json(run / "internal/contract-record.json")
    confirmation = _read_json(run / "internal/contract-confirmation.json")
    if (record.get("status") != "activated" or record.get("task_contract") != contract
            or record.get("confirmation") != confirmation
            or record.get("activation", {}).get("run_id") != run.name
            or confirmation.get("source") not in {"user_reply", "user_explicit_bypass", "auto_unambiguous"}
            or confirmation.get("confirmed_revision") != record.get("revision")):
        raise PipelineError("source scope is not bound to the activated contract confirmation")
    return contract

def _snapshots(run: Path, contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    receipt = _read_json(run / "internal/source-snapshots.json")
    if receipt.get("coverage_gaps"):
        raise PipelineError("snapshot coverage gaps block denominator")
    raw_snapshots = receipt.get("snapshots")
    if not isinstance(raw_snapshots, list): raise PipelineError("malformed snapshot receipt")
    out: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set(); seen_paths: set[str] = set(); seen_manifests: set[str] = set()
    for item in raw_snapshots:
        if not isinstance(item, dict): raise PipelineError("malformed snapshot receipt")
        explicit_repo=item.get("repository")
        manifest = item.get("manifest")
        raw = item.get("snapshot_dir")
        snapshot_id = item.get("snapshot_id")
        repo=explicit_repo if explicit_repo is not None else (manifest.get("repository") if isinstance(manifest,dict) else None)
        if (not isinstance(repo, str) or not isinstance(manifest, dict) or not isinstance(raw, str)
                or not isinstance(snapshot_id, str) or not snapshot_id
                or manifest.get("repository") != repo or (explicit_repo is not None and explicit_repo!=repo)):
            raise PipelineError("malformed snapshot receipt")
        path = Path(raw)
        snapshots_root = (run / "tmp/snapshots").resolve()
        try:
            resolved = path.resolve(strict=True); resolved.relative_to(snapshots_root)
        except (OSError, ValueError) as exc: raise PipelineError("snapshot escapes run") from exc
        manifest_id = manifest.get("content_sha256")
        if not isinstance(manifest_id,str) or not re.fullmatch(r"[0-9a-f]{64}",manifest_id):
            raise PipelineError("malformed snapshot manifest identity")
        if (repo in out or snapshot_id in seen_ids or str(resolved) in seen_paths or manifest_id in seen_manifests):
            raise PipelineError("duplicate snapshot repository/id/path/manifest")
        seen_ids.add(snapshot_id); seen_paths.add(str(resolved)); seen_manifests.add(manifest_id)
        if path.is_symlink() or manifest.get("commit_sha") != contract["repository_commits"].get(repo):
            raise PipelineError("snapshot commit binding mismatch")
        status = repository_runtime.snapshot_status(run.parents[2], run.name)
        if any(x.get("repository") == repo for x in status.get("coverage_gaps", [])):
            raise PipelineError("snapshot integrity failure")
        out[repo] = {"path": path, "manifest": manifest, "snapshot_id": snapshot_id}
    if set(out) != set(contract["repositories"]): raise PipelineError("missing commit snapshot")
    return out

def _envelope(kind: str, run_id: str, contract: dict[str, Any], payload: Any) -> dict[str, Any]:
    return {"artifact_type": kind, "schema_version": VERSION, "run_id": run_id,
            "contract_sha256": _digest(contract), "payload": payload,
            "payload_sha256": _digest(payload)}

def _pipeline_payload(path: Path, kind: str, run_id: str, contract: dict[str, Any]) -> dict[str, Any]:
    env=_read(path)
    if env.get("artifact_type")!=kind or env.get("run_id")!=run_id or env.get("contract_sha256")!=_digest(contract):
        raise PipelineError("pipeline artifact run/contract/type binding mismatch")
    payload=env.get("payload")
    if not isinstance(payload,dict): raise PipelineError("pipeline payload must be object")
    return payload

def _validate_denominator_boundary(run: Path, run_id: str, contract: dict[str,Any], initial: bool,
                                   inflight_repository: str|None=None) -> None:
    """Revalidate the complete denominator publication at execution boundaries.

    Current ledgers, the obligation index, and the assignment index are
    transactionally mutable after issue.  Their presence/schema/cross-binding
    is always checked; their original manifest hash is required only at the
    initial issue boundary.  Immutable denominator members always retain the
    committed manifest hash.
    """
    state=_pipeline_payload(run/"internal/denominator-state.json","publication_state",run_id,contract)
    if state.get("status")!="committed" or state.get("contract_sha256")!=_digest(contract):
        raise PipelineError("denominator publication is not committed")
    expected={"internal/source-bindings.json","internal/inventory-index.json","internal/obligation-index.json","internal/assignment-index.json"}
    for repo in contract["repositories"]:
        expected|={f"internal/inventories/{repo}.json",f"internal/ledgers/{repo}.json",f"internal/baseline-ledgers/{repo}.json"}
    refs={x["path"]:x["sha256"] for x in state["artifacts"]}
    if set(refs)!=expected or len(refs)!=len(state["artifacts"]): raise PipelineError("denominator publication manifest file set drift")
    mutable={"internal/obligation-index.json","internal/assignment-index.json"}|{f"internal/ledgers/{r}.json" for r in contract["repositories"]}
    require_initial=initial and not (run/"internal/context-publication-state.json").exists()
    for rel in sorted(expected):
        env=_read(run/rel)
        if (require_initial or rel not in mutable) and _digest(env)!=refs[rel]:
            raise PipelineError("denominator publication member hash drift: "+rel)
    snapshots=_snapshots(run,contract)
    oi=_pipeline_payload(run/"internal/obligation-index.json","obligation_index",run_id,contract)
    entries={x["repository"]:x for x in oi["repositories"]}
    if set(entries)!=set(contract["repositories"]): raise PipelineError("obligation index repository set drift")
    for repo in contract["repositories"]:
        inv=_pipeline_payload(run/"internal/inventories"/f"{repo}.json","inventory_artifact",run_id,contract)
        baseline=_pipeline_payload(run/"internal/baseline-ledgers"/f"{repo}.json","obligation_ledger_artifact",run_id,contract)
        current=_pipeline_payload(run/"internal/ledgers"/f"{repo}.json","obligation_ledger_artifact",run_id,contract)
        source_inventory.validate(inv,snapshots[repo]["path"])
        obligation_ledger.validate(baseline,inv,str(snapshots[repo]["path"])); obligation_ledger.validate(current,inv,str(snapshots[repo]["path"]))
        counts={s:sum(1 for row in current["obligations"] if row["status"]==s) for s in ("pending","assigned","complete")}
        if (repo!=inflight_repository and (entries[repo]["ledger_sha256"]!=_digest(current) or entries[repo]["obligation_count"]!=len(current["obligations"])
                or entries[repo]["status_counts"]!=counts)):
            raise PipelineError("obligation index/current ledger cross-binding drift")

def build_denominator(root: Path, run_id: str) -> dict[str, Any]:
    run = _root(root, run_id)
    with _run_lock(run):
        return _build_denominator_locked(root, run_id, run)

def _build_denominator_locked(root: Path, run_id: str, run: Path) -> dict[str, Any]:
    contract = _contract(run); snapshots = _snapshots(run, contract)
    state_path = run / "internal/denominator-state.json"
    inventories: list[dict[str, Any]] = []; ledgers: list[dict[str, Any]] = []
    artifacts: dict[str, dict[str, Any]] = {}
    for repo in sorted(contract["repositories"]):
        snapshot = snapshots[repo]["path"]
        inv = source_inventory.build(snapshot, repo, contract["repository_commits"][repo], contract["source_scopes"][repo])
        led = obligation_ledger.build(inv, str(snapshot))
        source_inventory.validate(inv, snapshot); obligation_ledger.validate(led, inv, str(snapshot))
        artifacts[f"internal/inventories/{repo}.json"] = _envelope("inventory_artifact", run_id, contract, inv)
        artifacts[f"internal/ledgers/{repo}.json"] = _envelope("obligation_ledger_artifact", run_id, contract, led)
        artifacts[f"internal/baseline-ledgers/{repo}.json"] = _envelope("obligation_ledger_artifact", run_id, contract, led)
        binding = {"repository": repo, "commit": inv["commit"], "snapshot_sha256": inv["snapshot_sha256"],
                   "snapshot_content_sha256": snapshots[repo]["manifest"].get("content_sha256"),
                   "snapshot_id": snapshots[repo]["snapshot_id"], "scope": inv["scope"],
                   "inventory_sha256": _digest(inv), "ledger_sha256": _digest(led)}
        counts={s:sum(1 for row in led["obligations"] if row["status"]==s) for s in ("pending","assigned","complete")}
        inventories.append(binding); ledgers.append({**binding,"obligation_count":len(led["obligations"]),"status_counts":counts})
    artifacts.update({
        "internal/source-bindings.json":_envelope("source_bindings",run_id,contract,{"repositories":inventories}),
        "internal/inventory-index.json":_envelope("inventory_index",run_id,contract,{"repositories":inventories}),
        "internal/obligation-index.json":_envelope("obligation_index",run_id,contract,{"repositories":ledgers}),
        "internal/assignment-index.json":_envelope("assignment_index",run_id,contract,{"assignments":[]}),
    })
    manifest=[{"path":rel,"sha256":_digest(env)} for rel,env in sorted(artifacts.items())]
    publishing={"status":"publishing","contract_sha256":_digest(contract),"artifacts":manifest}
    recovered=state_path.exists()
    if recovered:
        state=_pipeline_payload(state_path,"publication_state",run_id,contract)
        if state.get("status") not in {"publishing","committed"} or {**state,"status":"publishing"} != publishing:
            raise PipelineError("denominator publication state conflicts with deterministic manifest")
    else:
        state=publishing; _write(state_path,_envelope("publication_state",run_id,contract,state))
    for rel,expected in sorted(artifacts.items()):
        path=run/rel
        if path.exists():
            if _read(path) != expected: raise PipelineError("denominator artifact differs from publication manifest: "+rel)
        else:
            if state["status"]=="committed": raise PipelineError("committed denominator artifact missing: "+rel)
            _write(path,expected)
        if expected["artifact_type"]=="inventory_artifact":
            source_inventory.validate(expected["payload"],snapshots[expected["payload"]["repository"]]["path"])
        elif expected["artifact_type"]=="obligation_ledger_artifact":
            repo=expected["payload"]["repository"]
            obligation_ledger.validate(expected["payload"],artifacts[f"internal/inventories/{repo}.json"]["payload"],str(snapshots[repo]["path"]))
        _fault("build:"+Path(rel).name)
    for directory in ("inventories","ledgers","baseline-ledgers"):
        actual={p.name for p in (run/"internal"/directory).glob("*.json")}
        expected={f"{repo}.json" for repo in contract["repositories"]}
        if actual != expected: raise PipelineError("denominator managed file set drift: "+directory)
    if state["status"]!="committed":
        _write(state_path,_envelope("publication_state",run_id,contract,{**publishing,"status":"committed"}))
    return {"run_id":run_id,"repositories":len(inventories),"obligations":sum(x["obligation_count"] for x in ledgers),**({"recovered":True} if recovered else {})}

def _skills() -> dict[str, dict[str, str]]:
    base = Path(__file__).resolve().parents[1] / ".opencode/skills"
    result: dict[str, dict[str, str]] = {}
    for skill in source_inventory._SKILLS:
        path = base / skill / "SKILL.md"
        if not path.is_file() or path.is_symlink(): raise PipelineError(f"trusted storage skill missing: {skill}")
        refs = sorted((base / skill / "references").glob("*.md")) if (base / skill / "references").is_dir() else []
        # Skill instructions name the checklist as required; include every
        # local Markdown reference deterministically so the receipt binds the
        # actual material visible to the worker, not just its entry point.
        parts = [("SKILL.md", path.read_text(encoding="utf-8"))]
        for ref in refs:
            if ref.is_symlink() or not ref.is_file(): raise PipelineError("unsafe skill reference")
            parts.append(("references/" + ref.name, ref.read_text(encoding="utf-8")))
        content = "\n\n".join("## " + name + "\n" + text for name, text in parts)
        result[skill] = {"version": "sha256:" + hashlib.sha256(content.encode()).hexdigest(), "content": content}
    return result

def _load_repo(run: Path, contract: dict[str, Any], repo: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    inv = _pipeline_payload(run / "internal/inventories" / f"{repo}.json","inventory_artifact",run.name,contract)
    led = _pipeline_payload(run / "internal/ledgers" / f"{repo}.json","obligation_ledger_artifact",run.name,contract)
    snapshots = _snapshots(run, contract)
    if not isinstance(inv, dict) or not isinstance(led, dict): raise PipelineError("invalid persisted denominator")
    source_inventory.validate(inv, snapshots[repo]["path"]); obligation_ledger.validate(led, inv, str(snapshots[repo]["path"]))
    return inv, led, snapshots[repo]["path"]

def _baseline_ledger(run: Path, contract: dict[str, Any], repo: str, inv: dict[str, Any], snapshot: Path) -> dict[str, Any]:
    led = _pipeline_payload(run / "internal/baseline-ledgers" / f"{repo}.json","obligation_ledger_artifact",run.name,contract)
    if not isinstance(led, dict): raise PipelineError("invalid baseline ledger")
    obligation_ledger.validate(led, inv, str(snapshot))
    return led

def _output_schema() -> str:
    return compact_protocol.analysis_fragment_schema()

def _candidate(pack: dict[str,Any], receipts: list[dict[str,Any]], injected: dict[str,Any],
               skills: dict[str,dict[str,str]], compact: dict[str,Any], ordinal_map: dict[str,Any]) -> dict[str,Any]:
    enriched=copy.deepcopy(injected)
    for row in enriched["skills"]:
        trusted=skills[row["skill_id"]]
        row["version"]=trusted["version"]
        row["content_sha256"]=hashlib.sha256(trusted["content"].encode()).hexdigest()
    schema=_output_schema()
    return {"protocol_version":compact_protocol.CANDIDATE_PROTOCOL_VERSION,
            "output_schema":schema,"output_schema_sha256":hashlib.sha256(schema.encode()).hexdigest(),
            "instructions":compact_protocol.CANDIDATE_INSTRUCTIONS,
            "context_pack":pack,"skill_receipts":receipts,"injected":enriched,
            "compact_context":compact,"compact_context_sha256":compact_protocol.digest(compact),
            "ordinal_map":ordinal_map,"ordinal_map_sha256":compact_protocol.digest(ordinal_map),
            "adapter_version":compact_protocol.VERSION}

def _validate_candidate(candidate: dict[str,Any], inv: dict[str,Any], ledger: dict[str,Any], snapshot: Path,
                        skills: dict[str,dict[str,str]]) -> None:
    try: compact_protocol.validate_candidate_static(
        candidate,expected_ordinal_map=compact_protocol.ordinal_map(inv,ledger),
    )
    except compact_protocol.CompactProtocolError as exc:
        raise PipelineError(str(exc)) from exc
    pack=candidate["context_pack"]; receipts=candidate["skill_receipts"]
    try: context_budget.validate(pack,inv,ledger,str(snapshot),receipts,skills)
    except Exception as exc: raise PipelineError("candidate context pack is invalid") from exc
    expected=context_budget._injected(pack,str(snapshot),receipts,skills)
    enriched=copy.deepcopy(expected)
    for row in enriched["skills"]:
        trusted=skills[row["skill_id"]]; row["version"]=trusted["version"]
        row["content_sha256"]=hashlib.sha256(trusted["content"].encode()).hexdigest()
    if candidate["injected"]!=enriched:
        raise PipelineError("candidate source/skill injection drift")

def issue_context(root: Path, run_id: str, worker_id: str = "analysis-worker") -> dict[str, Any]:
    if not worker_id or "/" in worker_id or ".." in worker_id: raise PipelineError("unsafe worker id")
    run = _root(root, run_id)
    with _run_lock(run):
        return _issue_context_locked(root, run_id, worker_id, run)

def _issue_context_locked(root: Path, run_id: str, worker_id: str, run: Path) -> dict[str, Any]:
    contract = _contract(run); _snapshots(run, contract)
    _validate_denominator_boundary(run,run_id,contract,initial=True)
    state = _pipeline_payload(run/"internal/denominator-state.json","publication_state",run_id,contract)
    if state.get("status") != "committed" or state.get("contract_sha256") != _digest(contract):
        raise PipelineError("denominator is not atomically committed")
    existing_env=run/"internal/assignment-index.json"
    existing_payload=_pipeline_payload(existing_env,"assignment_index",run_id,contract)
    existing=existing_payload.get("assignments")
    if not isinstance(existing, list) or any(not isinstance(x, dict) for x in existing):
        raise PipelineError("invalid assignment index")
    existing_by_id = {x.get("fragment_id"): x for x in existing}
    if None in existing_by_id or len(existing_by_id) != len(existing):
        raise PipelineError("duplicate assignment")
    issued: list[dict[str,Any]]=[]; contexts: list[tuple[Path,dict[str,Any]]]=[]; skills=_skills()
    repository_plans: dict[str,dict[str,Any]]={}
    for repo in sorted(contract["repositories"]):
        inv, _current, snapshot = _load_repo(run, contract, repo)
        ledger = _baseline_ledger(run, contract, repo, inv, snapshot)
        rows = {x["obligation_id"]: x for x in ledger["obligations"]}; items = {x["inventory_id"]: x for x in inv["items"]}
        try: repo_plan, planned_contexts=compact_protocol.capacity_plan(inv,ledger,snapshot,run_id,skills)
        except compact_protocol.CompactProtocolError as exc: raise PipelineError(str(exc)) from exc
        repository_plans[repo]=repo_plan; mapping=compact_protocol.ordinal_map(inv,ledger)
        action_by_ordinal={row["ordinal"]:row for row in mapping["actions"]}
        item_by_ordinal={row["ordinal"]:row for row in mapping["items"]}
        for planned in planned_contexts:
            fid=planned["fragment_id"]
            chosen=[action_by_ordinal[value]["obligation_id"] for value in planned["action_ordinals"]]
            if any(rows[oid]["status"]!="pending" for oid in chosen): raise PipelineError("compact plan includes non-pending obligation")
            by_skill: dict[str, list[str]] = {}
            for oid in chosen:
                for skill in items[rows[oid]["inventory_id"]]["storage_skill_triggers"]: by_skill.setdefault(skill, []).append(oid)
            receipts = [fragment_runtime.skill_receipt(skill, sorted({rows[oid]["inventory_id"] for oid in oids}), sorted(oids), skills, "Apply only to the exact source range in this pack.") for skill, oids in sorted(by_skill.items())]
            selected_inventory_ids=[item_by_ordinal[value]["inventory_id"] for value in planned["item_ordinals"]]
            ranges = [{"inventory_id":items[iid]["inventory_id"],"path":items[iid]["path"],"line_start":items[iid]["line_start"],"line_end":items[iid]["line_end"]} for iid in selected_inventory_ids]
            try:
                pack=context_budget.build(inv,ledger,str(snapshot),chosen,ranges,receipts,skills,run_id,fid)
            except context_budget.ContextError as exc:
                raise PipelineError("compact atomic item group exceeds the context budget") from exc
            if pack["input_budget_tokens"] > 180000 or pack["output_budget_tokens"] != 4096: raise PipelineError("context budget exceeded")
            injected = context_budget._injected(pack, str(snapshot), receipts, skills)
            candidate=_candidate(pack,receipts,injected,skills,planned["compact_context"],mapping)
            _validate_candidate(candidate,inv,ledger,snapshot,skills)
            payload = _envelope("context_pack_artifact", run_id, contract, {"candidate": candidate, "candidate_sha256": _digest(candidate)})
            contexts.append((run/"internal/context-packs"/fid/"CONTEXT.json",payload))
            issued.append({"fragment_id": fid, "repository": repo, "obligation_ids": chosen, "worker_id": worker_id,
                                "context_pack_sha256": _digest(pack), "candidate_sha256": _digest(candidate), "ledger_sha256": _digest(ledger), "status": "issued",
                                "skill_receipt_ids": [x["receipt_id"] for x in receipts], "overhead_measurement_status": "reserved_not_measured"})
    auditor_calls=(len(issued)*compact_protocol.WORKER_CLAIM_LIMIT+compact_protocol.AUDITOR_CLAIM_LIMIT-1)//compact_protocol.AUDITOR_CLAIM_LIMIT
    worst=sum(compact_protocol.FIXED_MODEL_CALL_CAPS.values())+len(issued)+auditor_calls
    if len(issued)>compact_protocol.ANALYSIS_WORKER_CALL_LIMIT or auditor_calls>compact_protocol.SEMANTIC_AUDITOR_CALL_LIMIT or worst>compact_protocol.MAX_MODEL_CALLS:
        raise PipelineError("compact evaluator call closure exceeds frozen budget")
    capacity={"version":compact_protocol.VERSION,"repositories":repository_plans,"analysis_worker_calls":len(issued),
              "semantic_auditor_calls":auditor_calls,"fixed_model_call_caps":dict(compact_protocol.FIXED_MODEL_CALL_CAPS),
              "worst_model_calls":worst,"max_model_calls":compact_protocol.MAX_MODEL_CALLS,
              "native_output_byte_limit":compact_protocol.NATIVE_OUTPUT_BYTE_LIMIT,
              "input_byte_limit":compact_protocol.INPUT_BYTE_LIMIT,
              "maximum_compact_input_bytes":max(plan["maximum_compact_input_bytes"] for plan in repository_plans.values()),
              "maximum_native_output_bytes":max(plan["maximum_native_output_bytes"] for plan in repository_plans.values())}
    issued_by_id={x["fragment_id"]:x for x in issued}
    if set(existing_by_id)-set(issued_by_id): raise PipelineError("assignment outside deterministic issue plan")
    # Existing applied records are legitimate descendants of the exact issued
    # record; every immutable binding must still match.
    for row in issued:
        prior=existing_by_id.get(row["fragment_id"])
        if prior is None: continue
        if prior.get("status")=="issued":
            if prior!=row: raise PipelineError("existing issued assignment binding drift")
        elif prior.get("status")=="applied":
            core={k:prior[k] for k in row}; core["status"]="issued"
            if core!=row or set(prior)!=set(row)|{"fragment_sha256","applied_ledger_sha256"}:
                raise PipelineError("existing applied assignment binding drift")
        else: raise PipelineError("unknown assignment status")
    target_payload={"assignments":issued}
    target_env=_envelope("assignment_index",run_id,contract,target_payload)
    previous_sha=_digest(_envelope("assignment_index",run_id,contract,existing_payload))
    plan_path=run/"internal/context-publication-state.json"
    if plan_path.exists():
        previous_sha=_pipeline_payload(plan_path,"publication_state",run_id,contract).get("previous_assignment_sha256")
    plan={"status":"publishing","worker_id":worker_id,"previous_assignment_sha256":previous_sha,
          "assignment_sha256":_digest(target_env),
          "assignments":[{"fragment_id":x["fragment_id"],"sha256":_digest(x)} for x in issued],
          "contexts":[{"fragment_id":p.parent.name,"sha256":_digest(v)} for p,v in contexts],
          "capacity_plan":capacity}
    if plan_path.exists():
        prior_plan=_pipeline_payload(plan_path,"publication_state",run_id,contract)
        # previous hash is the denominator state on first publication and is
        # immutable even after apply transactions evolve the live index.
        comparison={**prior_plan,"status":"publishing"}
        if comparison != plan: raise PipelineError("context publication plan drift")
        plan=prior_plan
    else:
        _write(plan_path,_envelope("publication_state",run_id,contract,plan)); _fault("issue:planned")
    for path,payload in contexts:
        if path.exists():
            if _read(path)!=payload: raise PipelineError("published context drift")
        else:
            if plan["status"]=="committed": raise PipelineError("committed context missing")
            _write(path,payload)
        _fault("issue:context:"+path.parent.name)
    live=_read(existing_env)
    if plan["status"]=="publishing":
        if _digest(live) not in {plan["previous_assignment_sha256"],plan["assignment_sha256"]}:
            raise PipelineError("assignment index is not an allowed publication phase")
        if _digest(live)!=plan["assignment_sha256"]: _write(existing_env,target_env)
        _fault("issue:assignment-index")
        _write(plan_path,_envelope("publication_state",run_id,contract,{**plan,"status":"committed"}))
    else:
        # After commit, apply transactions may have changed only status and
        # their two journal-bound hashes; the immutable issue bindings remain.
        live_payload=_pipeline_payload(existing_env,"assignment_index",run_id,contract)
        live_by={x["fragment_id"]:x for x in live_payload["assignments"]}
        if set(live_by)!=set(issued_by_id): raise PipelineError("committed assignment set drift")
        for fid,row in issued_by_id.items():
            actual=live_by[fid]
            if actual.get("status")=="issued" and actual!=row: raise PipelineError("committed issued assignment drift")
            if actual.get("status")=="applied":
                core={k:actual[k] for k in row}; core["status"]="issued"
                if core!=row: raise PipelineError("committed applied assignment drift")
    return {"run_id":run_id,"assignments":len(issued),"overhead_measurement_status":"reserved_not_measured"}

def apply_fragment(root: Path, run_id: str, fragment_path: Path) -> dict[str, Any]:
    run = _root(root, run_id)
    # A journal is intentionally retained after success as a tamper-evident
    # receipt.  Re-entry only accepts an exact fragment hash and completes a
    # journalled transaction; it never guesses from ``complete`` rows.
    with _run_lock(run):
        return _apply_fragment_locked(root, run_id, fragment_path, run)


def _require_exact_regular_files(directory:Path,expected:set[str],label:str) -> None:
    if directory.is_symlink() or not directory.is_dir(): raise PipelineError(f"{label} directory missing")
    members=list(directory.iterdir())
    if (any(path.is_symlink() or not path.is_file() for path in members)
            or {path.name for path in members}!=expected):
        raise PipelineError(f"{label} directory is not an exact member closure")

def validate_run_for_judge(root:Path,run_id:str) -> dict[str,Any]:
    """Reconstruct the original publication and replay every committed transaction."""
    run=_root(root,run_id)
    with _run_lock(run):
        contract=_contract(run); _snapshots(run,contract)
        _validate_denominator_boundary(run,run_id,contract,initial=False)
        _validate_initial_denominator_publication(run,run_id,contract)
        denominator=_pipeline_payload(run/"internal/denominator-state.json","publication_state",run_id,contract)
        publication=_pipeline_payload(run/"internal/context-publication-state.json","publication_state",run_id,contract)
        if denominator.get("status")!="committed" or publication.get("status")!="committed": raise PipelineError("R2 publication incomplete")
        assignments=_pipeline_payload(run/"internal/assignment-index.json","assignment_index",run_id,contract)["assignments"]
        replay_ledgers,replay_assignments,replay_oi,replay_order=_replay_transaction_history(run,run_id,contract)
        live_ledgers={repo:_pipeline_payload(run/"internal/ledgers"/f"{repo}.json","obligation_ledger_artifact",run_id,contract)
                      for repo in sorted(contract["repositories"])}
        live_assignments={"assignments":assignments}
        live_oi=_pipeline_payload(run/"internal/obligation-index.json","obligation_index",run_id,contract)
        if live_ledgers!=replay_ledgers or live_assignments!=replay_assignments or live_oi!=replay_oi:
            raise PipelineError("live ledger/assignment/obligation index differs from canonical transaction replay")
        fragment_ids={path.stem for path in (run/"internal/fragments").glob("*.json")}
        tx_paths=list((run/"internal/transactions").glob("*.json")); tx_ids={path.stem for path in tx_paths}
        assignment_ids={x["fragment_id"] for x in assignments}
        if fragment_ids!=assignment_ids or tx_ids!=assignment_ids or set(replay_order)!=assignment_ids:
            raise PipelineError("fragment/transaction/assignment durable set mismatch")
        repositories=set(contract["repositories"])
        for name in ("inventories","ledgers","baseline-ledgers"):
            _require_exact_regular_files(run/f"internal/{name}",{repo+".json" for repo in repositories},name)
        for name in ("fragments","transactions","telemetry"):
            _require_exact_regular_files(run/f"internal/{name}",{fid+".json" for fid in assignment_ids},name)
        for name in ("compact-native-outputs","compact-adapter-receipts"):
            _require_exact_regular_files(run/f"internal/{name}",{fid+".json" for fid in assignment_ids},name)
        context_root=run/"internal/context-packs"
        if context_root.is_symlink() or not context_root.is_dir(): raise PipelineError("context-packs directory missing")
        context_members=list(context_root.iterdir())
        if (any(path.is_symlink() or not path.is_dir() for path in context_members)
                or {path.name for path in context_members}!=assignment_ids):
            raise PipelineError("context-packs directory is not an exact member closure")
        for path in context_members:
            _require_exact_regular_files(path,{"CONTEXT.json"},f"context-pack {path.name}")
        for path in tx_paths:
            if _pipeline_payload(path,"pipeline_transaction",run_id,contract).get("state")!="committed":
                raise PipelineError("uncommitted transaction blocks Judge")
        skills=_skills(); checked=0; referenced_execution_receipts:set[str]=set()
        for assignment in assignments:
            if assignment.get("status")!="applied": raise PipelineError("unapplied assignment blocks Judge")
            fid=assignment["fragment_id"]; repo=assignment["repository"]
            inv,ledger,snapshot=_load_repo(run,contract,repo); baseline=_baseline_ledger(run,contract,repo,inv,snapshot)
            fragment=_pipeline_payload(run/f"internal/fragments/{fid}.json","fragment_artifact",run_id,contract)
            if assignment.get("fragment_sha256")!=_digest(fragment): raise PipelineError("assignment fragment hash mismatch")
            stored=_pipeline_payload(run/f"internal/context-packs/{fid}/CONTEXT.json","context_pack_artifact",run_id,contract)
            candidate=stored["candidate"]; pack=candidate["context_pack"]; receipts=candidate["skill_receipts"]
            if stored["candidate_sha256"]!=_digest(candidate) or assignment["candidate_sha256"]!=_digest(candidate): raise PipelineError("candidate replay mismatch")
            fragment_runtime.validate(fragment,pack,inv,baseline,str(snapshot),receipts,skills)
            native_env=_read_json(run/f"internal/compact-native-outputs/{fid}.json");raw_native=native_env.get("raw_native")
            canonical_native=native_env.get("canonical_native")
            adapter=_read_json(run/f"internal/compact-adapter-receipts/{fid}.json")
            if (set(native_env)!={"artifact_type","schema_version","fragment_id","raw_native","canonical_native"}
                    or native_env.get("artifact_type")!="compact_native_output"
                    or native_env.get("schema_version")!="1.0" or native_env.get("fragment_id")!=fid):
                raise PipelineError("compact native output envelope is invalid")
            try:
                replayed_native=compact_protocol.canonicalize_native(raw_native,candidate["compact_context"])
                expanded=compact_protocol.expand_native(canonical_native,candidate["compact_context"],candidate["ordinal_map"],pack)
            except compact_protocol.CompactProtocolError as exc: raise PipelineError(str(exc)) from exc
            if (replayed_native!=canonical_native or expanded!=fragment or adapter!={"artifact_type":"compact_adapter_receipt","schema_version":"1.0","fragment_id":fid,
                    "raw_native_output_sha256":_digest(raw_native),
                    "canonical_native_output_sha256":_digest(canonical_native),"adapter_version":compact_protocol.VERSION,
                    "ordinal_map_sha256":candidate["ordinal_map_sha256"],"expanded_fragment_sha256":_digest(fragment),
                    "execution_receipt_sha256":adapter.get("execution_receipt_sha256")}):
                raise PipelineError("compact adapter replay mismatch")
            rows={x["obligation_id"]:x for x in ledger["obligations"]}
            if any(rows[o].get("assigned_fragment_id")!=fid or rows[o].get("status")!="complete" for o in assignment["obligation_ids"]):
                raise PipelineError("ledger fragment projection mismatch")
            telemetry=_read_json(run/f"internal/telemetry/{fid}.json")
            fragment_runtime.validate_runner_telemetry(telemetry,fragment,assignment["candidate_sha256"])
            if telemetry.get("context_sha256")!=_digest(_read(run/f"internal/context-packs/{fid}/CONTEXT.json")):
                raise PipelineError("runner telemetry CONTEXT binding mismatch")
            receipt_hash=telemetry["execution_receipt_sha256"]
            referenced_execution_receipts.add(receipt_hash)
            attestation=_read_json(run/f"internal/execution-receipts/{receipt_hash}.json")
            try: verified_hash,execution=fragment_runtime.verify_execution_attestation(attestation,"analysis-worker")
            except fragment_runtime.FragmentError as exc: raise PipelineError(str(exc)) from exc
            bindings={row["name"]:row["payload_sha256"] for row in execution["artifact_bindings"]}
            if (verified_hash!=receipt_hash or execution["session_id"]!=telemetry["session_id"]
                    or execution["output_payload_sha256"]!=_digest(raw_native)
                    or bindings!={"COMPACT_CONTEXT.json":candidate["compact_context_sha256"]}
                    or adapter.get("execution_receipt_sha256")!=receipt_hash):
                raise PipelineError("runner execution attestation binding mismatch")
            checked+=1
        semantic_dir=run/"internal/semantic-assessments"
        if semantic_dir.exists():
            if semantic_dir.is_symlink() or not semantic_dir.is_dir(): raise PipelineError("invalid semantic assessment directory")
            for path in semantic_dir.iterdir():
                if path.is_symlink() or not path.is_file() or path.suffix!=".json":
                    raise PipelineError("unexpected semantic assessment directory member")
                assessment=_read_json(path); auditor=assessment.get("auditor_telemetry",{}) if isinstance(assessment,dict) else {}
                receipt_hash=auditor.get("execution_receipt_sha256")
                if not isinstance(receipt_hash,str): raise PipelineError("semantic assessment lacks execution receipt")
                referenced_execution_receipts.add(receipt_hash)
        receipt_dir=run/"internal/execution-receipts"
        if receipt_dir.is_symlink() or not receipt_dir.is_dir(): raise PipelineError("execution receipt directory missing")
        actual_members=list(receipt_dir.iterdir())
        if (any(path.is_symlink() or not path.is_file() for path in actual_members)
                or {path.name for path in actual_members}!={value+".json" for value in referenced_execution_receipts}):
            raise PipelineError("execution receipt directory is not an exact reference closure")
        for receipt_hash in referenced_execution_receipts:
            attestation=_read_json(receipt_dir/f"{receipt_hash}.json")
            agent=attestation.get("receipt",{}).get("agent") if isinstance(attestation,dict) else None
            if agent not in {"analysis-worker","auditor"}: raise PipelineError("invalid execution receipt role")
            try: verified_hash,_=fragment_runtime.verify_execution_attestation(attestation,agent)
            except fragment_runtime.FragmentError as exc: raise PipelineError(str(exc)) from exc
            if verified_hash!=receipt_hash: raise PipelineError("execution receipt filename hash mismatch")
        return {"run_id":run_id,"repositories":len(contract["repositories"]),"assignments":checked,"status":"verified"}

def _validate_initial_denominator_publication(run:Path,run_id:str,contract:dict[str,Any]) -> None:
    """Rebuild every initially published denominator member and verify its manifest hash."""
    state=_pipeline_payload(run/"internal/denominator-state.json","publication_state",run_id,contract)
    refs={row["path"]:row["sha256"] for row in state.get("artifacts",[])}
    source=_pipeline_payload(run/"internal/source-bindings.json","source_bindings",run_id,contract)
    inventory_index=_pipeline_payload(run/"internal/inventory-index.json","inventory_index",run_id,contract)
    if source!=inventory_index: raise PipelineError("source binding/inventory index drift")
    by_repo={row["repository"]:row for row in source["repositories"]}
    if len(by_repo)!=len(source["repositories"]) or set(by_repo)!=set(contract["repositories"]):
        raise PipelineError("source binding repository closure mismatch")
    expected={
        "internal/source-bindings.json":_envelope("source_bindings",run_id,contract,source),
        "internal/inventory-index.json":_envelope("inventory_index",run_id,contract,inventory_index),
        "internal/assignment-index.json":_envelope("assignment_index",run_id,contract,{"assignments":[]}),
    }
    oi=[]
    for repo in sorted(contract["repositories"]):
        inv=_pipeline_payload(run/"internal/inventories"/f"{repo}.json","inventory_artifact",run_id,contract)
        baseline=_pipeline_payload(run/"internal/baseline-ledgers"/f"{repo}.json","obligation_ledger_artifact",run_id,contract)
        binding=by_repo[repo]
        if binding["inventory_sha256"]!=_digest(inv) or binding["ledger_sha256"]!=_digest(baseline):
            raise PipelineError("initial source binding payload drift")
        counts={s:sum(1 for row in baseline["obligations"] if row["status"]==s) for s in ("pending","assigned","complete")}
        oi.append({**copy.deepcopy(binding),"obligation_count":len(baseline["obligations"]),"status_counts":counts})
        expected[f"internal/inventories/{repo}.json"]=_envelope("inventory_artifact",run_id,contract,inv)
        expected[f"internal/ledgers/{repo}.json"]=_envelope("obligation_ledger_artifact",run_id,contract,baseline)
        expected[f"internal/baseline-ledgers/{repo}.json"]=_envelope("obligation_ledger_artifact",run_id,contract,baseline)
    expected["internal/obligation-index.json"]=_envelope("obligation_index",run_id,contract,{"repositories":oi})
    if set(refs)!=set(expected): raise PipelineError("denominator publication path closure mismatch")
    for rel,env in expected.items():
        if refs[rel]!=_digest(env): raise PipelineError("denominator initial publication hash mismatch: "+rel)

def _issued_form(record: dict[str,Any]) -> dict[str,Any]:
    value={k:v for k,v in record.items() if k not in {"fragment_sha256","applied_ledger_sha256"}}
    value["status"]="issued"
    return value

def _publication_assignment(run: Path, run_id: str, contract: dict[str,Any], record: dict[str,Any]) -> None:
    publication=_pipeline_payload(run/"internal/context-publication-state.json","publication_state",run_id,contract)
    if publication.get("status")!="committed": raise PipelineError("context publication is not committed")
    issued=_issued_form(record); fid=issued["fragment_id"]
    refs=[x for x in publication["assignments"] if x["fragment_id"]==fid]
    contexts=[x for x in publication["contexts"] if x["fragment_id"]==fid]
    if len(refs)!=1 or refs[0]["sha256"]!=_digest(issued) or len(contexts)!=1:
        raise PipelineError("assignment is not exactly registered by committed publication")
    context_path=run/"internal/context-packs"/fid/"CONTEXT.json"
    if _digest(_read(context_path))!=contexts[0]["sha256"]:
        raise PipelineError("committed context publication hash mismatch")

_TX_STATES=("prepared","ledger_published","assignment_published","obligation_published","committed")

def _validate_transaction(tx: dict[str,Any], fragment: dict[str,Any], assignment_ids: list[str]) -> None:
    pairs=(("old_ledger","old_ledger_sha256"),("new_ledger","new_ledger_sha256"),
           ("old_assignment_index","old_assignment_index_sha256"),("new_assignment_index","new_assignment_index_sha256"),
           ("old_obligation_index","old_obligation_index_sha256"),("new_obligation_index","new_obligation_index_sha256"),
           ("old_selected_rows","old_selected_rows_sha256"),("new_selected_rows","new_selected_rows_sha256"))
    if (tx.get("fragment_sha256")!=_digest(fragment) or tx.get("state") not in _TX_STATES
            or tx.get("transaction_id")!="txn-"+_digest(fragment)[:16]
            or tx.get("run_id")!=fragment.get("run_id") or tx.get("fragment_id")!=fragment.get("fragment_id")):
        raise PipelineError("transaction fragment/state binding mismatch")
    for value_key,hash_key in pairs:
        if tx.get(hash_key)!=_digest(tx.get(value_key)): raise PipelineError("transaction embedded hash mismatch: "+value_key)
    def selected(ledger: dict[str,Any]) -> list[dict[str,Any]]:
        return [r for r in ledger.get("obligations",[]) if r.get("obligation_id") in assignment_ids]
    if (tx["old_selected_rows"]!=selected(tx["old_ledger"]) or tx["new_selected_rows"]!=selected(tx["new_ledger"])
            or len(tx["old_selected_rows"])!=len(assignment_ids) or len(tx["new_selected_rows"])!=len(assignment_ids)):
        raise PipelineError("transaction selected-row closure mismatch")
    fid=tx["fragment_id"]; repo=tx["repository"]
    if tx["old_ledger"].get("repository")!=repo or tx["new_ledger"].get("repository")!=repo:
        raise PipelineError("transaction repository drift")
    old_records={x["fragment_id"]:x for x in tx["old_assignment_index"]["assignments"]}
    new_records={x["fragment_id"]:x for x in tx["new_assignment_index"]["assignments"]}
    if set(old_records)!=set(new_records) or fid not in old_records:
        raise PipelineError("transaction assignment set drift")
    expected=copy.deepcopy(old_records[fid]); expected.update({"status":"applied","fragment_sha256":tx["fragment_sha256"],"applied_ledger_sha256":tx["new_ledger_sha256"]})
    if old_records[fid].get("status")!="issued" or new_records[fid]!=expected or any(old_records[k]!=new_records[k] for k in old_records if k!=fid):
        raise PipelineError("transaction assignment transition drift")
    old_by={r["obligation_id"]:r for r in tx["old_ledger"]["obligations"]}; new_by={r["obligation_id"]:r for r in tx["new_ledger"]["obligations"]}
    if set(old_by)!=set(new_by) or any(old_by[k]!=new_by[k] for k in old_by if k not in assignment_ids):
        raise PipelineError("transaction changes unselected ledger rows")
    if any(old_by[k].get("status")!="pending" or new_by[k].get("status")!="complete" or new_by[k].get("assigned_fragment_id")!=fid for k in assignment_ids):
        raise PipelineError("transaction selected-row transition drift")
    old_oi={x["repository"]:x for x in tx["old_obligation_index"]["repositories"]}; new_oi={x["repository"]:x for x in tx["new_obligation_index"]["repositories"]}
    if set(old_oi)!=set(new_oi) or repo not in old_oi or any(old_oi[k]!=new_oi[k] for k in old_oi if k!=repo):
        raise PipelineError("transaction obligation index set drift")
    expected_oi=copy.deepcopy(old_oi[repo]); expected_oi["ledger_sha256"]=tx["new_ledger_sha256"]
    expected_oi["obligation_count"]=len(tx["new_ledger"]["obligations"])
    expected_oi["status_counts"]={s:sum(1 for row in tx["new_ledger"]["obligations"] if row["status"]==s) for s in ("pending","assigned","complete")}
    if old_oi[repo].get("ledger_sha256")!=tx["old_ledger_sha256"] or new_oi[repo]!=expected_oi:
        raise PipelineError("transaction obligation index transition drift")

def _transaction_phase(tx: dict[str,Any], ledger: dict[str,Any], assignments: dict[str,Any], oi: dict[str,Any]) -> int:
    current=(_digest(ledger),_digest(assignments),_digest(oi))
    stages=(
        (tx["old_ledger_sha256"],tx["old_assignment_index_sha256"],tx["old_obligation_index_sha256"]),
        (tx["new_ledger_sha256"],tx["old_assignment_index_sha256"],tx["old_obligation_index_sha256"]),
        (tx["new_ledger_sha256"],tx["new_assignment_index_sha256"],tx["old_obligation_index_sha256"]),
        (tx["new_ledger_sha256"],tx["new_assignment_index_sha256"],tx["new_obligation_index_sha256"]),
    )
    matches=[i for i,value in enumerate(stages) if value==current]
    if len(matches)!=1: raise PipelineError("transaction files do not match an enumerated publication phase")
    phase=matches[0]; state_phase=_TX_STATES.index(tx["state"])
    allowed={0:{0,1},1:{1,2},2:{2,3},3:{3},4:{3}}[state_phase]
    if phase not in allowed: raise PipelineError("transaction journal state/file phase mismatch")
    return phase

def _canonical_replay_base(run: Path, run_id: str, contract: dict[str,Any]) -> tuple[dict[str,dict[str,Any]],dict[str,Any],dict[str,Any]]:
    """Rebuild the issued state without trusting any transaction payload.

    The immutable source bindings, baseline ledgers, and committed context
    publication are sufficient to reconstruct both mutable indexes.  This is
    the root used when transaction receipts are replayed below.
    """
    publication=_pipeline_payload(run/"internal/context-publication-state.json","publication_state",run_id,contract)
    if publication.get("status")!="committed" or publication.get("worker_id")!="analysis-worker":
        raise PipelineError("transaction replay requires committed analysis-worker contexts")
    context_refs={x["fragment_id"]:x for x in publication["contexts"]}
    if len(context_refs)!=len(publication["contexts"]): raise PipelineError("duplicate replay context reference")
    snapshots=_snapshots(run,contract); skills=_skills(); ledgers: dict[str,dict[str,Any]]={}
    baselines: dict[str,dict[str,Any]]={}; inventories: dict[str,dict[str,Any]]={}
    for repo in sorted(contract["repositories"]):
        inv=_pipeline_payload(run/"internal/inventories"/f"{repo}.json","inventory_artifact",run_id,contract)
        baseline=_baseline_ledger(run,contract,repo,inv,snapshots[repo]["path"])
        inventories[repo]=inv; baselines[repo]=baseline; ledgers[repo]=copy.deepcopy(baseline)
    assignments: list[dict[str,Any]]=[]
    for ref in publication["assignments"]:
        fid=ref["fragment_id"]; context_ref=context_refs.get(fid)
        if context_ref is None: raise PipelineError("published assignment lacks replay context")
        path=run/"internal/context-packs"/fid/"CONTEXT.json"; env=_read(path)
        if _digest(env)!=context_ref["sha256"]: raise PipelineError("replay context publication hash mismatch")
        stored=_pipeline_payload(path,"context_pack_artifact",run_id,contract)
        candidate=stored["candidate"]; pack=candidate["context_pack"]; repo=pack["repository"]
        if repo not in inventories: raise PipelineError("replay context repository drift")
        _validate_candidate(candidate,inventories[repo],baselines[repo],snapshots[repo]["path"],skills)
        if stored["candidate_sha256"]!=_digest(candidate): raise PipelineError("replay candidate hash mismatch")
        record={"fragment_id":fid,"repository":repo,"obligation_ids":pack["obligation_ids"],
                "worker_id":"analysis-worker","context_pack_sha256":_digest(pack),
                "candidate_sha256":_digest(candidate),"ledger_sha256":_digest(baselines[repo]),
                "status":"issued","skill_receipt_ids":[x["receipt_id"] for x in candidate["skill_receipts"]],
                "overhead_measurement_status":"reserved_not_measured"}
        if _digest(record)!=ref["sha256"]: raise PipelineError("published assignment cannot be canonically reconstructed")
        assignments.append(record)
    bindings=_pipeline_payload(run/"internal/source-bindings.json","source_bindings",run_id,contract)["repositories"]
    by_repo={x["repository"]:x for x in bindings}
    if set(by_repo)!=set(contract["repositories"]): raise PipelineError("replay source binding set drift")
    oi_rows=[]
    for repo in sorted(contract["repositories"]):
        baseline=baselines[repo]; binding=by_repo[repo]
        if binding["ledger_sha256"]!=_digest(baseline): raise PipelineError("replay baseline binding drift")
        counts={s:sum(1 for row in baseline["obligations"] if row["status"]==s) for s in ("pending","assigned","complete")}
        oi_rows.append({**copy.deepcopy(binding),"obligation_count":len(baseline["obligations"]),"status_counts":counts})
    return ledgers,{"assignments":assignments},{"repositories":oi_rows}

def _replay_transaction_history(run: Path, run_id: str, contract: dict[str,Any]) -> tuple[dict[str,dict[str,Any]],dict[str,Any],dict[str,Any],list[str]]:
    """Replay every durable fragment from the canonical issued state.

    Global assignment and obligation indexes form a total transaction chain.
    Each receipt must match the state derived from baseline plus all preceding
    durable fragments; self-consistent edits to a receipt therefore cannot
    redefine its old state.
    """
    ledgers,assignments,oi=_canonical_replay_base(run,run_id,contract)
    directory=run/"internal/transactions"; pending: dict[str,tuple[Path,dict[str,Any],dict[str,Any]]]={}
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir(): raise PipelineError("invalid transaction directory")
        for path in sorted(directory.iterdir(),key=lambda value:value.name):
            if path.suffix!=".json": raise PipelineError("unexpected transaction directory member")
            tx=_pipeline_payload(path,"pipeline_transaction",run_id,contract); fid=tx.get("fragment_id")
            if not isinstance(fid,str) or path.name!=fid+".json" or fid in pending:
                raise PipelineError("transaction file identity drift")
            fragment=_pipeline_payload(run/"internal/fragments"/f"{fid}.json","fragment_artifact",run_id,contract)
            pending[fid]=(path,tx,fragment)
    order: list[str]=[]; skills=_skills(); snapshots=_snapshots(run,contract)
    while pending:
        matches=[]
        for fid,(_,tx,_) in pending.items():
            repo=tx.get("repository")
            if (repo in ledgers and tx.get("old_ledger_sha256")==_digest(ledgers[repo])
                    and tx.get("old_assignment_index_sha256")==_digest(assignments)
                    and tx.get("old_obligation_index_sha256")==_digest(oi)):
                matches.append(fid)
        if len(matches)!=1: raise PipelineError("transaction history is not one deterministic chain")
        fid=matches[0]; _,tx,fragment=pending.pop(fid); repo=tx["repository"]
        records=[x for x in assignments["assignments"] if x["fragment_id"]==fid]
        if len(records)!=1: raise PipelineError("transaction assignment is not canonical")
        ids=records[0]["obligation_ids"]; _validate_transaction(tx,fragment,ids)
        if (tx["old_ledger"]!=ledgers[repo] or tx["old_assignment_index"]!=assignments
                or tx["old_obligation_index"]!=oi):
            raise PipelineError("transaction old state is not baseline plus prior durable fragments")
        inv=_pipeline_payload(run/"internal/inventories"/f"{repo}.json","inventory_artifact",run_id,contract)
        baseline=_baseline_ledger(run,contract,repo,inv,snapshots[repo]["path"])
        stored=_pipeline_payload(run/"internal/context-packs"/fid/"CONTEXT.json","context_pack_artifact",run_id,contract)
        candidate=stored["candidate"]; _validate_candidate(candidate,inv,baseline,snapshots[repo]["path"],skills)
        pack=candidate["context_pack"]; receipts=candidate["skill_receipts"]
        fragment_runtime.validate(fragment,pack,inv,baseline,str(snapshots[repo]["path"]),receipts,skills)
        receipt_by={r["receipt_id"]:r for r in receipts}
        receipt_map={oid:[ref["receipt_id"] for ref in pack["skill_receipts"] if oid in receipt_by[ref["receipt_id"]]["obligation_ids"]] for oid in ids}
        next_ledger=obligation_ledger._apply_validated(copy.deepcopy(ledgers[repo]),fragment,inv,str(snapshots[repo]["path"]),receipt_map)
        next_assignments=copy.deepcopy(assignments); next_record=next(x for x in next_assignments["assignments"] if x["fragment_id"]==fid)
        next_record.update({"status":"applied","fragment_sha256":_digest(fragment),"applied_ledger_sha256":_digest(next_ledger)})
        next_oi=copy.deepcopy(oi); entries=[x for x in next_oi["repositories"] if x["repository"]==repo]
        if len(entries)!=1: raise PipelineError("transaction replay obligation repository drift")
        entries[0]["ledger_sha256"]=_digest(next_ledger); entries[0]["obligation_count"]=len(next_ledger["obligations"])
        entries[0]["status_counts"]={s:sum(1 for row in next_ledger["obligations"] if row["status"]==s) for s in ("pending","assigned","complete")}
        selected=[r for r in next_ledger["obligations"] if r["obligation_id"] in ids]
        if (tx["new_ledger"]!=next_ledger or tx["new_assignment_index"]!=next_assignments
                or tx["new_obligation_index"]!=next_oi or tx["new_selected_rows"]!=selected):
            raise PipelineError("transaction new state is not the durable fragment replay result")
        ledgers[repo]=next_ledger; assignments=next_assignments; oi=next_oi; order.append(fid)
    return ledgers,assignments,oi,order

def _recover_transaction(run: Path, run_id: str, contract: dict[str,Any], path: Path,
                         tx: dict[str,Any], fragment: dict[str,Any]) -> None:
    repo=tx["repository"]
    ledger=_pipeline_payload(run/"internal/ledgers"/f"{repo}.json","obligation_ledger_artifact",run_id,contract)
    assignments=_pipeline_payload(run/"internal/assignment-index.json","assignment_index",run_id,contract)
    oi=_pipeline_payload(run/"internal/obligation-index.json","obligation_index",run_id,contract)
    ids=next((x["obligation_ids"] for x in tx["new_assignment_index"]["assignments"] if x["fragment_id"]==tx["fragment_id"]),None)
    if not isinstance(ids,list): raise PipelineError("transaction lacks its assignment")
    _validate_transaction(tx,fragment,ids)
    inv,_,snapshot=_load_repo(run,contract,repo)
    try:
        obligation_ledger.validate(tx["old_ledger"],inv,str(snapshot)); obligation_ledger.validate(tx["new_ledger"],inv,str(snapshot))
    except Exception as exc: raise PipelineError("transaction embeds an invalid ledger") from exc
    # Rebuild the entire transaction chain from immutable baselines and every
    # durable fragment.  This validates both the old and new sides of this
    # receipt instead of treating the journal's old payload as an authority.
    replay_ledgers,replay_assignments,replay_oi,replay_order=_replay_transaction_history(run,run_id,contract)
    if tx["state"]=="committed":
        live_ledgers={name:_pipeline_payload(run/"internal/ledgers"/f"{name}.json","obligation_ledger_artifact",run_id,contract) for name in contract["repositories"]}
        if live_ledgers!=replay_ledgers or assignments!=replay_assignments or oi!=replay_oi:
            raise PipelineError("committed transaction history does not reproduce live state")
        return
    if not replay_order or replay_order[-1]!=tx["fragment_id"]:
        raise PipelineError("unfinished transaction is not the end of durable history")
    phase=_transaction_phase(tx,ledger,assignments,oi)
    if phase==0:
        _write(run/"internal/ledgers"/f"{repo}.json",_envelope("obligation_ledger_artifact",run_id,contract,tx["new_ledger"]))
        tx={**tx,"state":"ledger_published"}; _write(path,_envelope("pipeline_transaction",run_id,contract,tx)); _fault("ledger_published"); phase=1
    if phase==1:
        _write(run/"internal/assignment-index.json",_envelope("assignment_index",run_id,contract,tx["new_assignment_index"]))
        tx={**tx,"state":"assignment_published"}; _write(path,_envelope("pipeline_transaction",run_id,contract,tx)); _fault("assignment_published"); phase=2
    if phase==2:
        _write(run/"internal/obligation-index.json",_envelope("obligation_index",run_id,contract,tx["new_obligation_index"]))
        tx={**tx,"state":"obligation_published"}; _write(path,_envelope("pipeline_transaction",run_id,contract,tx)); _fault("obligation_published"); phase=3
    if tx["state"]!="committed":
        tx={**tx,"state":"committed"}; _write(path,_envelope("pipeline_transaction",run_id,contract,tx))

def _apply_fragment_locked(root: Path, run_id: str, fragment_path: Path, run: Path) -> dict[str, Any]:
    contract=_contract(run); fragment=_read_fragment_import(fragment_path,run); fragment_hash=_digest(fragment)
    fid=fragment.get("fragment_id")
    if not isinstance(fid,str): raise PipelineError("fragment id missing")
    index_payload=_pipeline_payload(run/"internal/assignment-index.json","assignment_index",run_id,contract)
    selected=[x for x in index_payload["assignments"] if x["fragment_id"]==fid]
    if len(selected)!=1: raise PipelineError("fragment is stale, duplicate, or unassigned")
    assignment=selected[0]; repo=assignment["repository"]
    journal_path=run/"internal/transactions"/f"{fid}.json"
    _validate_denominator_boundary(run,run_id,contract,initial=False,inflight_repository=(repo if journal_path.exists() else None))
    _publication_assignment(run,run_id,contract,assignment)
    if journal_path.exists():
        tx=_pipeline_payload(journal_path,"pipeline_transaction",run_id,contract)
        stored=_pipeline_payload(run/"internal/fragments"/f"{fid}.json","fragment_artifact",run_id,contract)
        if stored!=fragment or tx.get("fragment_sha256")!=fragment_hash: raise PipelineError("transaction replay content differs")
        _recover_transaction(run,run_id,contract,journal_path,tx,fragment)
        return {"run_id":run_id,"fragment_id":fid,"repository":repo,"applied":True,"recovered":True}
    if assignment["status"]!="issued": raise PipelineError("applied assignment lacks its durable transaction")
    if fragment.get("run_id")!=run_id or fragment.get("worker_instance")!=assignment["worker_id"] or fragment.get("obligation_ids")!=assignment["obligation_ids"]:
        raise PipelineError("cross-run/worker/assignment fragment")
    inv,ledger,snapshot=_load_repo(run,contract,repo)
    baseline=_baseline_ledger(run,contract,repo,inv,snapshot)
    if _digest(baseline)!=assignment["ledger_sha256"]: raise PipelineError("baseline ledger binding drift")
    context_env=_read(run/"internal/context-packs"/fid/"CONTEXT.json")
    stored=context_env["payload"]; candidate=stored["candidate"]
    if stored["candidate_sha256"]!=_digest(candidate) or stored["candidate_sha256"]!=assignment["candidate_sha256"]:
        raise PipelineError("candidate context drift")
    skills=_skills(); _validate_candidate(candidate,inv,baseline,snapshot,skills)
    pack=candidate["context_pack"]; receipts=candidate["skill_receipts"]
    if _digest(pack)!=assignment["context_pack_sha256"] or fragment.get("context_pack_sha256")!=_digest(pack): raise PipelineError("context pack drift")
    if [x["receipt_id"] for x in receipts]!=assignment["skill_receipt_ids"]: raise PipelineError("skill receipt drift")
    fragment_runtime.validate(fragment,pack,inv,baseline,str(snapshot),receipts,skills)
    receipt_by={r["receipt_id"]:r for r in receipts}
    receipt_map={oid:[ref["receipt_id"] for ref in pack["skill_receipts"] if oid in receipt_by[ref["receipt_id"]]["obligation_ids"]] for oid in assignment["obligation_ids"]}
    old_ledger=copy.deepcopy(ledger)
    updated=obligation_ledger._apply_validated(copy.deepcopy(ledger),fragment,inv,str(snapshot),receipt_map)
    old_assignments=copy.deepcopy(index_payload); new_assignments=copy.deepcopy(index_payload)
    new_record=next(x for x in new_assignments["assignments"] if x["fragment_id"]==fid)
    new_record.update({"status":"applied","fragment_sha256":fragment_hash,"applied_ledger_sha256":_digest(updated)})
    old_oi=_pipeline_payload(run/"internal/obligation-index.json","obligation_index",run_id,contract)
    old_oi=copy.deepcopy(old_oi); new_oi=copy.deepcopy(old_oi)
    entries=[x for x in new_oi["repositories"] if x["repository"]==repo]
    if len(entries)!=1: raise PipelineError("obligation index repository drift")
    old_entries=[x for x in old_oi["repositories"] if x["repository"]==repo]
    if len(old_entries)!=1 or old_entries[0]["ledger_sha256"]!=_digest(old_ledger): raise PipelineError("obligation index old ledger binding drift")
    entry=entries[0]; entry["ledger_sha256"]=_digest(updated); entry["obligation_count"]=len(updated["obligations"])
    entry["status_counts"]={s:sum(1 for row in updated["obligations"] if row["status"]==s) for s in ("pending","assigned","complete")}
    old_rows=[r for r in old_ledger["obligations"] if r["obligation_id"] in assignment["obligation_ids"]]
    new_rows=[r for r in updated["obligations"] if r["obligation_id"] in assignment["obligation_ids"]]
    tx={"transaction_id":"txn-"+fragment_hash[:16],"run_id":run_id,"fragment_id":fid,"fragment_sha256":fragment_hash,"repository":repo,
        "old_ledger":old_ledger,"old_ledger_sha256":_digest(old_ledger),"new_ledger":updated,"new_ledger_sha256":_digest(updated),
        "old_assignment_index":old_assignments,"old_assignment_index_sha256":_digest(old_assignments),"new_assignment_index":new_assignments,"new_assignment_index_sha256":_digest(new_assignments),
        "old_obligation_index":old_oi,"old_obligation_index_sha256":_digest(old_oi),"new_obligation_index":new_oi,"new_obligation_index_sha256":_digest(new_oi),
        "old_selected_rows":old_rows,"old_selected_rows_sha256":_digest(old_rows),"new_selected_rows":new_rows,"new_selected_rows_sha256":_digest(new_rows),"state":"prepared"}
    _validate_transaction(tx,fragment,assignment["obligation_ids"])
    _write(run/"internal/fragments"/f"{fid}.json",_envelope("fragment_artifact",run_id,contract,fragment))
    _write(journal_path,_envelope("pipeline_transaction",run_id,contract,tx)); _fault("prepared")
    _recover_transaction(run,run_id,contract,journal_path,tx,fragment)
    return {"run_id":run_id,"fragment_id":fid,"repository":repo,"applied":True}
