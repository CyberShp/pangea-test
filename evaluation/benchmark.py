"""Evaluator-owned contracts and the sealed OpenCode execution runner.

The candidate only sees a hash-allowlisted public bundle.  Agent resolution,
environment receipts, native OpenCode JSONL telemetry and private scoring stay
under evaluator control.  The module never imports PANGEA's production
runtime.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
import fnmatch
import json
import math
import os
import re
import shlex
import shutil
import stat
import subprocess
import time
import tempfile
from types import MappingProxyType
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .codetalks_staging import CodeTalksStagingError, collect_final_output


ROOT = Path(__file__).resolve().parents[1]
FROZEN_CONFIG_PATH = ROOT / "benchmarks" / "evaluation" / "frozen-config.json"
CORPUS_MANIFEST_PATH = ROOT / "benchmarks" / "evaluation" / "corpus-manifest.json"
PUBLIC_FORBIDDEN_PATH_PARTS = {"oracles", "hidden-oracle", "hidden_oracle"}
OPENCODE_PROJECT_CONFIG_NAMES = {"opencode.json", "opencode.jsonc"}
OPENCODE_PLUGIN_DIRECTORY_NAMES = {"plugin", "plugins"}
PRIVATE_FIELD_NAMES = {"fault_mode", "evidence_keywords", "scoring", "sealed_oracle", "oracle_answer"}
PRIVATE_TEXT_MARKERS = re.compile(r"\b(?:fault_mode|evidence_keywords|sealed[_ -]?oracle|benchmarks[\\/]oracles|hidden[_ -]?oracle)\b", re.I)
NATIVE_EVENT_TYPES = {"step_start", "step_finish", "tool_use", "text", "reasoning", "error"}
NETWORK_TOOL_NAMES = {"webfetch", "websearch", "web", "fetch", "http", "browser"}
EQUAL_TOOLS = {"read", "glob", "grep"}
AS_SHIPPED_SAFE_TOOLS = {"read", "glob", "grep", "skill", "task", "bash"}
AS_SHIPPED_TASKS = {"analysis-worker", "auditor", "mr-reader"}
AS_SHIPPED_SKILLS = {
    "analysis-depth-contract", "c-cpp-analysis", "project-workspace",
    "report-contract", "risk-card", "test-asset-retrieval",
    "storage-spdk", "storage-nvme-cli", "storage-nvmeof", "storage-iscsi",
    "storage-resource-recovery", "storage-destructive-cli",
}
AS_SHIPPED_ROLE_TOOLS = {
    "primary": frozenset(AS_SHIPPED_SAFE_TOOLS),
    "analysis-worker": frozenset({"read", "glob", "grep"}),
    "auditor": frozenset({"read"}),
    "mr-reader": frozenset({"read", "glob", "grep", "bash"}),
}
COMPACT_EXECUTION_AGENTS = {
    "analysis-worker": "analysis-leaf",
    "auditor": "audit-leaf",
}
DEEPSEEK_MODEL = "deepseek/deepseek-v4-flash"
# The provider window is frozen at 200K.  Leaf roles deliberately retain a
# 20K reserve for evaluator framing/serialization; this 180K safety envelope
# is not a second or drifting model-window declaration.
FROZEN_CONTEXT_WINDOW = 200_000
ROLE_INPUT_SAFETY_LIMIT = 180_000
FROZEN_OUTPUT_LIMIT = 4_096
DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_CONFIG_SOURCE_ENV = "OPENCODE_CONFIG_PATH"
OPENCODE_CONFIG_SCHEMA_URL = "https://opencode.ai/config.json"
_DEEPSEEK_CONFIG_MAX_BYTES = 1_048_576
MANAGED_WRITE_ROOTS = {"pangea-data", "codetalks-data"}
FUSE_AGENT = "codetalks-fused-v2.4"
FUSE_SKILL = "codetalks-source-driven-blackbox-v2"
FUSE_SAFE_TOOLS = {"read", "glob", "grep", "skill", "task", "bash", "write", "edit"}
FORBIDDEN_TOOLS = {
    "shell", "edit", "write", "patch", "webfetch", "websearch",
    "browser", "todowrite", "question", "invalid",
}
EVALUATOR_INTAKE_COMMAND = "python3 runtime/runctl.py evaluator-intake-v2"

# Receipt-safe, finite classifications for rejected tool inputs.  These names
# deliberately describe only the policy class: never the submitted command,
# argument, path, prompt, process output, credential, or a digest of any of
# those values.
TOOL_INPUT_POLICY_CATEGORY_BY_DECISION = MappingProxyType({
    "deny:missing-or-unknown-input": "missing_or_malformed_input",
    "deny:missing-command": "missing_or_malformed_input",
    "deny:unparseable-command": "missing_or_malformed_input",
    "deny:workdir-outside-bundle": "path_scope",
    "deny:path-outside-bundle": "path_scope",
    "deny:path-outside-bundle-or-missing": "path_scope",
    "deny:option-path-outside-bundle": "path_scope",
    "deny:shell-composition-or-redirection": "shell_syntax",
    "deny:dangerous-executable": "executable_not_allowlisted",
    "deny:command-not-allowlisted": "executable_not_allowlisted",
    "deny:arbitrary-python": "runtime_command_not_allowlisted",
    "deny:invalid-codetalks-run-guard": "managed_runtime_contract",
    "deny:unknown-codetalks-run-guard-command": "managed_runtime_contract",
    "deny:invalid-codetalks-run-guard-options": "managed_runtime_contract",
    "deny:duplicate-codetalks-run-guard-option": "managed_runtime_contract",
    "deny:unknown-codetalks-run-guard-option": "managed_runtime_contract",
    "deny:codetalks-run-guard-scope": "managed_runtime_contract",
    "deny:git-subcommand": "readonly_command_contract",
    "deny:git-write-or-scope-option": "readonly_command_contract",
    "deny:rg-preprocessor": "readonly_command_contract",
    "deny:sed-in-place": "readonly_command_contract",
    "deny:sed-script-not-print-only": "readonly_command_contract",
    "deny:find-mutation-or-exec": "readonly_command_contract",
    "deny:worker-not-allowlisted": "worker_not_allowlisted",
    "deny:skill-not-frozen": "skill_not_frozen",
    "deny:write-outside-codetalks-data": "managed_output_scope",
    "deny:tool-not-allowlisted": "tool_not_allowlisted",
    "deny:intake-tool-not-allowlisted": "tool_not_allowlisted",
    "deny:intake-runtime-input": "managed_runtime_contract",
})
TOOL_INPUT_POLICY_CATEGORIES = frozenset(TOOL_INPUT_POLICY_CATEGORY_BY_DECISION.values())

# Sensitive provider credentials are declared separately so the isolated
# corpus scanner and child-environment policy cannot silently drift apart.
# The username is a credential identifier rather than a bearer secret, but a
# concrete long assignment is still candidate-private data and is scanned.
PROVIDER_SECRET_ENV_KEYS = {
    "DEEPSEEK_API_KEY", "OPENCODE_API_KEY", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "OPENCODE_SERVER_PASSWORD",
}
PROVIDER_CREDENTIAL_ENV_KEYS = PROVIDER_SECRET_ENV_KEYS | {"OPENCODE_SERVER_USERNAME"}
SCM_SECRET_ASSIGNMENT_KEYS = {"GITHUB_TOKEN", "GH_TOKEN"}
CANDIDATE_SECRET_ASSIGNMENT_KEYS = PROVIDER_CREDENTIAL_ENV_KEYS | SCM_SECRET_ASSIGNMENT_KEYS | {
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
}
SECRET_ASSIGNMENT_MARKER = re.compile(
    rb"(?im)(?:\b(?:"
    + b"|".join(re.escape(key.encode("ascii")) for key in sorted(CANDIDATE_SECRET_ASSIGNMENT_KEYS))
    + rb")\s*=|[\"'](?:"
    + b"|".join(re.escape(key.encode("ascii")) for key in sorted(CANDIDATE_SECRET_ASSIGNMENT_KEYS))
    + rb")[\"']\s*:)\s*([^\s#;]+)"
)
PRIVATE_KEY_MARKER = re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)
SECRET_PLACEHOLDER_MARKERS = (
    b"${", b"$(", b"<", b"placeholder", b"example", b"your_", b"your-",
    b"replace_me", b"replace-me", b"changeme", b"dummy", b"not-a-real", b"redacted",
    b"sample_", b"sample-", b"fake_", b"fake-", b"test_", b"test-", b"...",
)

# Values are passed to the child but never copied into a receipt.  Adding a
# provider secret requires updating PROVIDER_SECRET_ENV_KEYS, which
# automatically updates both this allowlist and candidate leakage scanning.
ENVIRONMENT_ALLOWLIST = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_ALL", "LC_CTYPE", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_CACHE_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY",
    "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "DEEPSEEK_API_KEY",
}


def contains_secret_assignment(data: bytes) -> bool:
    """Detect concrete long credential assignments, but not names/placeholders."""
    if PRIVATE_KEY_MARKER.search(data):
        return True
    for match in SECRET_ASSIGNMENT_MARKER.finditer(data):
        value = match.group(1).strip(b"'\"`,")
        lowered = value.lower()
        if len(value) < 12 or any(marker in lowered for marker in SECRET_PLACEHOLDER_MARKERS):
            continue
        if len(set(value)) < 6:
            continue
        return True
    return False


class BenchmarkContractError(ValueError):
    """Raised when an evaluator contract is malformed or would leak answers."""


@dataclass(frozen=True)
class SealedOracle:
    """Private evaluator input; construction is intentionally loader-only."""
    payload: dict[str, Any]
    receipt: dict[str, Any]


@dataclass(frozen=True)
class RunSpec:
    candidate: str
    track: str
    public_bundle: Path
    task: Path
    isolated_policy: Path
    case_id: str
    public_case_path: str
    public_case_sha256: str
    candidate_manifest_sha256: str | None = None
    candidate_materialization: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunReceipt:
    candidate: str
    track: str
    case_id: str
    command: list[str]
    exit_code: int
    duration_seconds: float
    stdout_sha256: str
    stderr_sha256: str
    telemetry: dict[str, Any]
    environment_keys: list[str]
    preflight: dict[str, Any]
    policy_receipt: dict[str, Any]
    passed: bool
    failures: list[str]


_PUBLIC_BUNDLE_BINDING_AUTHORITY = object()


class _ValidatedPublicBundleBinding:
    """Evaluator-owned immutable/public snapshot after one full validation."""
    __slots__ = ("root", "snapshot", "managed_root", "authority")

    def __init__(self, root: Path, snapshot: dict[str, Any], managed_root: str, authority: object) -> None:
        if authority is not _PUBLIC_BUNDLE_BINDING_AUTHORITY:
            raise BenchmarkContractError("public bundle binding is evaluator-owned")
        self.root = root
        self.snapshot = snapshot
        self.managed_root = managed_root
        self.authority = authority


_EXECUTION_AUTHORITY = object()
_EXECUTION_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
    "598eef9a4a4114705db534211b2967cb7e8af8b65ec770c1c8af4943026b9899"
))


class TrustedRoleExecution:
    """Opaque evaluator-owned process result consumed by trusted writers."""
    __slots__ = ("_receipt", "_stdout", "_authority", "_signature")

    def __init__(self, receipt: dict[str, Any], stdout: str, authority: object) -> None:
        if authority is not _EXECUTION_AUTHORITY:
            raise BenchmarkContractError("role execution receipts are evaluator-owned")
        self._receipt = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self._stdout = stdout
        self._authority = authority
        self._signature = _EXECUTION_PRIVATE_KEY.sign(self._receipt.encode()).hex()

    def _trusted_payload(self) -> tuple[dict[str,Any],str]:
        try:
            _EXECUTION_PRIVATE_KEY.public_key().verify(bytes.fromhex(self._signature),self._receipt.encode())
        except (InvalidSignature,ValueError):
            raise BenchmarkContractError("trusted auditor execution receipt required")
        payload=json.loads(self._receipt)
        if (self._authority is not _EXECUTION_AUTHORITY
                or payload.get("stdout_sha256")!=sha256(self._stdout.encode()).hexdigest()):
            raise BenchmarkContractError("trusted auditor execution receipt required")
        return payload,self._stdout

    @property
    def receipt(self) -> Mapping[str, Any]:
        payload,_=self._trusted_payload(); return MappingProxyType(payload)


def _persist_execution_attestation(run:Path,execution:TrustedRoleExecution,
                                   directory_name:str="execution-receipts") -> tuple[Path,str]:
    receipt,_=execution._trusted_payload(); receipt_hash=_canonical_hash(receipt)
    envelope={"artifact_type":"role_execution_attestation","schema_version":"1.0",
              "receipt":receipt,"signature":execution._signature}
    if directory_name not in {"execution-receipts", "final-audit-execution-receipts"}:
        raise BenchmarkContractError("invalid execution attestation directory")
    target=run/"internal"/directory_name/(receipt_hash+".json"); target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if _load_json(target)!=envelope: raise BenchmarkContractError("execution attestation conflict")
        return target,receipt_hash
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=target.parent,delete=False) as handle:
        json.dump(envelope,handle,ensure_ascii=False,indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno()); temp=Path(handle.name)
    os.replace(temp,target); os.chmod(target,0o400)
    return target,receipt_hash


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkContractError(f"cannot load {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkContractError(f"{path}: root must be an object")
    return data


def load_frozen_config(path: Path = FROZEN_CONFIG_PATH) -> dict[str, Any]:
    """Load the version-pinned, public evaluation configuration."""
    data = _load_json(path)
    required = {"schema_version", "reference", "targets", "runtime", "candidates", "fair_tracks", "scorecard"}
    missing = required - data.keys()
    if set(data) != required or data.get("schema_version") != "1.0":
        raise BenchmarkContractError(f"invalid frozen config; missing={sorted(missing)}")
    reference = data["reference"]
    expected_reference = {
        "archive": "codetalks-fused-v2.4-zh.zip",
        "sha256": "7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325",
        "verified_preset": {
            "path": "/Volumes/Media/codetalk-skill-first-agent-runtime/data/skills/presets/codetalks-v2.4",
            "preset_id": "codetalks-v2.4",
            "skill_id": "skill.codetalks-module-full-analysis",
            "skill_version": "skill_version_build_cd5236626f824050a1598a845d2b5eba",
            "content_digest": "sha256:8217e197c006884f845a141b967d498c0a3fa716ccb4dd924fbad11377b0fbfc",
        },
        "skill": {"name": FUSE_SKILL, "version": "2.4.0"},
        "runtime_status": "runnable-minimal-adapter",
        "runtime_agent": FUSE_AGENT,
        "adapter": {"path": ".opencode/agents/codetalks-fused-v2.4.md", "sha256": "77f1800fa911bd4fcaeff38963b9d16a36cd7e9890e0e3a99b76707764c8f941"},
        "limitation": "The verified preset remains corroborating evidence only. CodeTalks is evaluated as the uploaded Skill through the minimal frozen adapter.",
    }
    if reference != expected_reference:
        raise BenchmarkContractError("reference differs from the uploaded archive, verified preset, or comparator limitation")
    targets = data["targets"]
    if not isinstance(targets, list) or len(targets) != 2 or {item.get("id") for item in targets if isinstance(item, dict)} != {"spdk", "nvme-cli"}:
        raise BenchmarkContractError("frozen targets must be exactly spdk and nvme-cli")
    expected_repositories = {
        "spdk": "https://github.com/spdk/spdk",
        "nvme-cli": "https://github.com/linux-nvme/nvme-cli",
    }
    expected_commits = {
        "spdk": "97af299e3c76368219f0cddcc710fafd57edcc1c",
        "nvme-cli": "cc00f4fd5d8262c440d033de9504ebf641880e62",
    }
    expected_trees = {
        "spdk": "3718a94e7956cd5f15a1e8edb65d6bbeacef9c7d",
        "nvme-cli": "a0f34ca372b1fe44cba2bfd1be1a02c2ba808349",
    }
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"id", "repository", "commit", "tree"}:
            raise BenchmarkContractError("each target must have exact repository, commit, and tree fields")
        if len(str(target.get("commit", ""))) != 40 or len(str(target.get("tree", ""))) != 40:
            raise BenchmarkContractError("each target must have 40-character commit and tree ids")
    if {target["id"]: target["commit"] for target in targets} != expected_commits:
        raise BenchmarkContractError("target commits differ from frozen SPDK/nvme-cli revisions")
    if {target["id"]: target["tree"] for target in targets} != expected_trees:
        raise BenchmarkContractError("target trees differ from frozen SPDK/nvme-cli revisions")
    if {target["id"]: target["repository"] for target in targets} != expected_repositories:
        raise BenchmarkContractError("target repository URLs differ from frozen SPDK/nvme-cli repositories")
    runtime = data["runtime"]
    if runtime != {"client": "opencode", "provider": "deepseek", "provider_base_url": "https://api.deepseek.com", "opencode_version": "1.18.4", "opencode_debug_timeout_seconds": 120, "model": DEEPSEEK_MODEL, "context_window": FROZEN_CONTEXT_WINDOW, "max_output_tokens": FROZEN_OUTPUT_LIMIT, "max_model_calls": 40, "max_wall_clock_seconds": 1800}:
        raise BenchmarkContractError("runtime is not the frozen OpenCode 200K/4096 configuration")
    candidates = data["candidates"]
    if candidates != {"pangea": {"agent": "pangea-test"}, "fuse": {"agent": FUSE_AGENT}}:
        raise BenchmarkContractError("candidate agents differ from frozen configuration")
    fair_tracks = data["fair_tracks"]
    expected_tracks = {
        "equal-tools": {"candidate_visible_inputs": "public-corpus-only", "candidate_network": "disabled", "provider_transport": "required", "policy_mode": "agent-tool-override", "enabled_tools": ["read", "glob", "grep"], "max_tool_calls": 120},
        "as-shipped": {"candidate_visible_inputs": "public-corpus-only", "candidate_network": "disabled",
            "provider_transport": "required", "policy_mode": "candidate-aware-safety-overlay", "max_tool_calls": 120,
            "candidate_policies": {
                "pangea": {"preserve": ["candidate_prompt", "candidate_skills", "candidate_worker_topology"], "enabled_tools": ["read", "glob", "grep", "skill", "task", "bash"], "forbidden_enabled_tools": ["edit", "write", "patch", "webfetch", "websearch", "browser", "question", "invalid", "todowrite", "shell"], "task_allowlist": ["analysis-worker", "auditor", "mr-reader"], "conditional_task": "mr-reader", "skill_allowlist": sorted(AS_SHIPPED_SKILLS), "managed_write_roots": ["pangea-data"], "bash_policy": "event-audited-readonly-or-managed-runtime"},
                "fuse": {"preserve": ["candidate_prompt", "candidate_skills"], "enabled_tools": ["read", "glob", "grep", "skill", "task", "bash", "write", "edit"], "forbidden_enabled_tools": ["patch", "webfetch", "websearch", "browser", "question", "invalid", "todowrite", "shell"], "task_allowlist": ["general"], "skill_allowlist": [FUSE_SKILL], "managed_write_roots": ["codetalks-data"], "bash_policy": "codetalks-run-guard-or-readonly"},
            }},
    }
    if not isinstance(fair_tracks, list) or len(fair_tracks) != 2:
        raise BenchmarkContractError("fair tracks must contain equal-tools and as-shipped")
    actual_tracks = {item.get("id"): {key: value for key, value in item.items() if key != "id"} for item in fair_tracks if isinstance(item, dict)}
    if actual_tracks != expected_tracks:
        raise BenchmarkContractError("fair track tools, network, or call limits are not frozen")
    scorecard = data["scorecard"]
    expected_weights = {"recall": 30, "precision": 15, "evidence": 20, "blackbox_executability": 15, "flow_coverage": 10, "na_specificity": 10}
    if not isinstance(scorecard, dict) or scorecard.get("weights") != expected_weights or sum(expected_weights.values()) != 100:
        raise BenchmarkContractError("scorecard must retain the frozen 100-point weights")
    thresholds = scorecard.get("thresholds", {})
    expected_thresholds = {
        "absolute": {
            "minimum": {"score": 90, "critical_recall": 100, "high_recall": 95, "mutation_kill": 90, "supported_precision": 90, "evidence_refs": 100, "evidence_semantic_support": 97, "p0_recall": 95, "p1_recall": 90, "blackbox_executability": 90, "na_specificity": 95, "applicable_disposition": 100, "hc_contribution_retention": 100},
            "maximum": {"clean_fixed_unsupported_hc": 0, "hard_gate_regression": 0},
            "must_be_false": ["truncated", "invalid_json", "safety_regression"],
        },
        "versus_fuse": {
            "at_least": {"mean_score_delta": 0, "score_delta_lower_ci": -2, "hard_gate_regression": 0},
            "exceeds": {"mean_score_delta": 3, "score_delta_lower_ci_exclusive": 0, "core_case_win_rate": 70, "hard_gate_regression": 0},
        },
    }
    if thresholds != expected_thresholds:
        raise BenchmarkContractError("scorecard thresholds differ from the complete frozen gates")
    return data


def load_corpus_manifest(path: Path = CORPUS_MANIFEST_PATH) -> dict[str, Any]:
    """Load the isolated source corpus manifest without resolving source trees."""
    data = _load_json(path)
    if data.get("schema_version") != "1.0" or data.get("visibility") != "public-to-candidate":
        raise BenchmarkContractError("corpus manifest must be public-to-candidate schema 1.0")
    if set(data) not in ({"schema_version", "visibility", "repositories"}, {"schema_version", "visibility", "description", "repositories"}):
        raise BenchmarkContractError("corpus manifest has missing or additional properties")
    repositories = data.get("repositories")
    if not isinstance(repositories, list) or len(repositories) != 2:
        raise BenchmarkContractError("corpus manifest needs exactly two repositories")
    for repository in repositories:
        required = {"id", "url", "commit", "tree", "mount", "read_only"}
        if not isinstance(repository, dict) or set(repository) != required:
            raise BenchmarkContractError("invalid corpus repository entry")
        if len(str(repository["commit"])) != 40 or len(str(repository["tree"])) != 40 or repository["read_only"] is not True:
            raise BenchmarkContractError("corpus entries require pinned commits/trees and read_only=true")
        mount = Path(str(repository["mount"]))
        if mount.is_absolute() or ".." in mount.parts:
            raise BenchmarkContractError("corpus mounts must be relative and cannot traverse")
    if len({item["id"] for item in repositories}) != 2:
        raise BenchmarkContractError("corpus repository ids must be unique")
    frozen_targets = {item["id"]: item for item in load_frozen_config()["targets"]}
    actual = {item["id"]: item for item in repositories}
    if set(actual) != {"spdk", "nvme-cli"}:
        raise BenchmarkContractError("corpus repositories must be exactly spdk and nvme-cli")
    for repo_id, target in frozen_targets.items():
        repository = actual[repo_id]
        expected_mount = f"repositories/{repo_id}"
        if repository["commit"] != target["commit"] or repository["tree"] != target["tree"] or repository["mount"] != expected_mount:
            raise BenchmarkContractError("corpus commits, trees, and mounts must exactly match frozen targets")
        if _canonical_repository_url(str(repository["url"])) != _canonical_repository_url(str(target["repository"])):
            raise BenchmarkContractError("corpus repository URL differs from frozen target")
    return data


def _canonical_repository_url(value: str) -> str:
    value = value.strip()
    if value.startswith("git@") and ":" in value:
        value = "https://" + value[4:].replace(":", "/", 1)
    elif value.startswith("ssh://git@"):
        remainder = value[len("ssh://git@") :]
        value = "https://" + remainder.replace(":", "/", 1)
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value.lower()


def validate_public_bundle(bundle: Path) -> list[str]:
    """Verify a manifest allowlist and reject unsafe candidate-visible entries."""
    errors: list[str] = []
    if bundle.is_symlink() or not bundle.is_dir():
        return [f"public bundle does not exist: {bundle}"]
    manifest_path = bundle / "public-bundle-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return ["public bundle requires a regular public-bundle-manifest.json"]
    try:
        manifest = _load_json(manifest_path)
        allowed = manifest["files"]
        allowed_directories = manifest["directories"]
        if (
            set(manifest) != {"schema_version", "files", "directories"}
            or manifest.get("schema_version") != "1.0"
            or not isinstance(allowed, dict)
            or not isinstance(allowed_directories, list)
            or not all(isinstance(item, str) for item in allowed_directories)
            or len(set(allowed_directories)) != len(allowed_directories)
        ):
            raise BenchmarkContractError("invalid bundle manifest")
    except BenchmarkContractError as exc:
        return [str(exc)]
    observed: set[str] = set()
    observed_directories: set[str] = set()
    plugin_entries = set(_opencode_project_plugin_entries(bundle))
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle).as_posix()
        relative_path = path.relative_to(bundle)
        if path == manifest_path:
            continue
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            errors.append(f"cannot inspect entry {relative}: {exc}")
            continue
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            errors.append(f"unsafe bundle entry: {relative}")
            continue
        if path.is_dir():
            observed_directories.add(relative)
            if relative in plugin_entries:
                errors.append(f"OpenCode project plugin entry exposed: {relative}")
            continue
        observed.add(relative)
        relative_parts = {part.lower() for part in relative_path.parts}
        if relative in plugin_entries:
            errors.append(f"OpenCode project plugin entry exposed: {relative}")
            continue
        if relative_parts & PUBLIC_FORBIDDEN_PATH_PARTS:
            errors.append(f"private path exposed: {relative}")
            continue
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                errors.append(f"cannot inspect text {relative}: {exc}")
                continue
            repository_payload = len(relative_path.parts) >= 3 and relative_path.parts[0] == "repositories"
            if PRIVATE_TEXT_MARKERS.search(text):
                errors.append(f"private oracle marker exposed: {relative}")
            if not repository_payload and contains_secret_assignment(text.encode("utf-8")):
                errors.append(f"candidate or evaluator secret assignment exposed: {relative}")
            # Frozen repositories are opaque source payloads.  Real projects
            # legitimately contain JSON streams/JSONL and fragments carrying
            # a .json suffix.  Evaluator/candidate metadata outside that mount
            # retains the strict single-document JSON and private-field gate.
            if path.suffix == ".json" and not repository_payload:
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as exc:
                    errors.append(f"cannot inspect JSON {relative}: {exc}")
                    continue
                if _contains_private_field(value):
                    errors.append(f"private oracle field exposed: {relative}")
        expected = allowed.get(relative)
        if not isinstance(expected, str) or sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f"bundle manifest hash mismatch: {relative}")
    if observed != set(allowed):
        errors.append("bundle manifest allowlist does not exactly match visible files")
    if observed_directories != set(allowed_directories):
        errors.append("bundle manifest allowlist does not exactly match visible directories")
    return errors


def _opencode_project_plugin_entries(root: Path) -> list[str]:
    """Return every project-level external-plugin entry recognized by 1.18.4.

    OpenCode 1.18.4 searches upward for ``opencode.json{,c}``, loads the same
    names from every discovered ``.opencode`` directory, and scans immediate
    ``{plugin,plugins}/*.{ts,js}`` children there.  Rejecting the configuration
    files and the whole auto-discovery directories also closes file/directory
    plugin specs whose package.json or index entry would otherwise be resolved
    from a candidate-controlled configuration.  Agents, commands, and skills
    remain valid frozen ``.opencode`` inputs.
    """
    if root.is_symlink() or not root.is_dir():
        return []
    entries: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        folded = tuple(part.casefold() for part in relative.parts)
        if path.name.casefold() in OPENCODE_PROJECT_CONFIG_NAMES:
            entries.add(relative.as_posix())
            continue
        for index, part in enumerate(folded[:-1]):
            if (part == ".opencode" and index + 1 < len(folded)
                    and folded[index + 1] in OPENCODE_PLUGIN_DIRECTORY_NAMES):
                entries.add(relative.as_posix())
                break
    return sorted(entries)


def write_public_bundle_manifest(bundle: Path) -> Path:
    """Create a hash allowlist after strict filesystem inspection (test harness use)."""
    if not bundle.is_dir():
        raise BenchmarkContractError("bundle must be a directory")
    files: dict[str, str] = {}
    directories: list[str] = []
    manifest = bundle / "public-bundle-manifest.json"
    for path in bundle.rglob("*"):
        if path == manifest:
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BenchmarkContractError(f"unsafe bundle entry: {path.relative_to(bundle)}")
        if path.is_dir():
            directories.append(path.relative_to(bundle).as_posix())
        elif path.is_file():
            files[path.relative_to(bundle).as_posix()] = sha256(path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "files": dict(sorted(files.items())),
        "directories": sorted(directories),
    }, indent=2) + "\n", encoding="utf-8")
    return manifest


def _contains_private_field(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & PRIVATE_FIELD_NAMES) or any(_contains_private_field(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_field(item) for item in value)
    return False


def build_opencode_command(task_file: Path, public_bundle: Path, candidate: str, track: str,
                           config: dict[str, Any] | None = None, *, validate_bundle: bool = True) -> list[str]:
    """Build the actual non-interactive OpenCode JSON command, without running it."""
    frozen = load_frozen_config()
    if config is not None and config != frozen:
        raise BenchmarkContractError("caller supplied a configuration that differs from frozen evaluator config")
    config = frozen
    runtime = config["runtime"]
    if candidate not in config["candidates"]:
        raise BenchmarkContractError(f"unknown frozen candidate: {candidate}")
    agent = config["candidates"][candidate].get("agent")
    if not isinstance(agent, str) or not agent:
        raise BenchmarkContractError(
            f"{candidate} comparator runtime is unresolved; freeze an exact runnable agent config and hash before execution"
        )
    if track not in {item["id"] for item in config["fair_tracks"]}:
        raise BenchmarkContractError(f"unknown frozen track: {track}")
    if not task_file.is_file():
        raise BenchmarkContractError(f"task file does not exist: {task_file}")
    if not public_bundle.is_dir():
        raise BenchmarkContractError(f"public bundle does not exist: {public_bundle}")
    try:
        task_file.resolve().relative_to(public_bundle.resolve())
    except ValueError as exc:
        raise BenchmarkContractError("task file must be inside the public bundle") from exc
    if validate_bundle:
        leaks = validate_public_bundle(public_bundle)
        if leaks:
            raise BenchmarkContractError("public bundle failed leakage validation: " + "; ".join(leaks))
    # The evaluator supplies exactly one private plugin through the isolated
    # config.  ``--pure`` cannot be used here because OpenCode 1.18.4 disables
    # every external plugin under that flag, including explicit config paths.
    return ["opencode", "run", "--dir", str(public_bundle), "--agent", agent, "--model", runtime["model"], "--format", "json", "--print-logs", task_file.read_text(encoding="utf-8")]


def _regular_external_file(path: Path, public_bundle: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise BenchmarkContractError(f"isolated policy must be a regular file: {path}")
    try:
        path.resolve().relative_to(public_bundle.resolve())
    except ValueError:
        return
    raise BenchmarkContractError("isolated policy cannot be candidate-visible")


def _canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _track(config: dict[str, Any], track_id: str, candidate: str = "pangea") -> dict[str, Any]:
    try:
        track = next(item for item in config["fair_tracks"] if item["id"] == track_id)
    except StopIteration as exc:
        raise BenchmarkContractError(f"unknown frozen track: {track_id}") from exc
    policies = track.get("candidate_policies")
    if policies is None:
        return track
    if candidate not in policies:
        raise BenchmarkContractError("track has no frozen candidate policy")
    return {**{key: value for key, value in track.items() if key != "candidate_policies"}, **policies[candidate]}


def _equal_tools_override(agent: str) -> dict[str, Any]:
    known = EQUAL_TOOLS | FORBIDDEN_TOOLS | {"skill"}
    tools = {name: name in EQUAL_TOOLS for name in sorted(known)}
    permission = {"external_directory": "deny"}
    return {"permission": permission, "agent": {agent: {"tools": tools, "permission": permission}}}


def _as_shipped_safety_overlay(primary_agent: str, workers: Iterable[str], *,
                               primary_task_enabled: bool = True,
                               primary_phase: str | None = None) -> dict[str, Any]:
    """Agent-scoped harness overlay; it does not replace prompts, skills, or agents."""
    known = AS_SHIPPED_SAFE_TOOLS | FORBIDDEN_TOOLS
    common: dict[str, Any] = {
        "edit": "deny", "write": "deny", "patch": "deny",
        "webfetch": "deny", "websearch": "deny", "browser": "deny",
        "question": "deny", "invalid": "deny", "todowrite": "deny", "shell": "deny",
        "external_directory": "deny",
    }
    permissions: dict[str, Any] = {**common,
        "read": "allow", "glob": "allow", "grep": "allow",
        "skill": {"*": "deny", **{name: "allow" for name in sorted(AS_SHIPPED_SKILLS)}},
        "task": ({"*": "deny", **{name: "allow" for name in sorted(AS_SHIPPED_TASKS)}}
                 if primary_task_enabled else
                 {"*": "deny", **{name: "deny" for name in sorted(AS_SHIPPED_TASKS)}}),
        "bash": {
            "*": "deny",
            "git status*": "allow", "git diff*": "allow", "git log*": "allow",
            "git show*": "allow", "git rev-parse*": "allow", "git ls-files*": "allow",
            "git grep*": "allow", "rg *": "allow", "find *": "allow", "sed *": "allow",
            "ls *": "allow", "head *": "allow", "tail *": "allow", "wc *": "allow",
            "python* runtime/runctl.py *": "allow",
            "python* -m tooling.pangea_cli *": "allow",
            "*/python* runtime/runctl.py *": "allow",
            "*/python* -m tooling.pangea_cli *": "allow",
            "*;*": "deny", "*&&*": "deny", "*||*": "deny", "*|*": "deny",
            "*>*": "deny", "*<*": "deny", "*`*": "deny", "*$*": "deny",
            "*~*": "deny", "*..*": "deny", "*=/*": "deny", "*=..*": "deny",
            "*curl*": "deny", "*wget*": "deny", "* nc *": "deny",
            "*netcat*": "deny", "*ssh*": "deny", "*scp*": "deny",
        },
    }
    if primary_phase == "intake":
        permissions = {
            **common, "read": "deny", "glob": "deny", "grep": "deny",
            "skill": {"*": "deny"},
            "task": {"*": "deny", **{name: "deny" for name in sorted(AS_SHIPPED_TASKS)}},
            "bash": {"*": "deny", EVALUATOR_INTAKE_COMMAND: "allow"},
        }
    elif primary_phase not in {None, "resume", "finalize"}:
        raise BenchmarkContractError("unknown primary phase overlay")
    def leaf(*,glob:bool=False,grep:bool=False,bash:dict[str,str]|str="deny") -> dict[str,Any]:
        return {**common,"read":"allow","glob":"allow" if glob else "deny","grep":"allow" if grep else "deny",
                "skill":{"*":"deny"},"task":{"*":"deny"},"bash":bash}
    readonly_git={"*":"deny","git status*":"allow","git diff*":"allow","git log*":"allow","git show*":"allow",
                  "git rev-parse*":"allow","git ls-files*":"allow","git grep*":"allow"}
    permission_by_agent={primary_agent:permissions,"analysis-worker":leaf(glob=True,grep=True),
                         "auditor":leaf(),"mr-reader":leaf(glob=True,grep=True,bash=readonly_git)}
    agents={}
    for name in [primary_agent,*sorted(set(workers))]:
        role="primary" if name==primary_agent else name
        if role not in AS_SHIPPED_ROLE_TOOLS: raise BenchmarkContractError("unknown as-shipped role")
        enabled=AS_SHIPPED_ROLE_TOOLS[role]
        if role == "primary" and primary_phase == "intake":
            enabled = frozenset({"bash"})
        elif role == "primary" and not primary_task_enabled:
            enabled = frozenset(set(enabled) - {"task"})
        agents[name]={"tools":{tool:tool in enabled for tool in sorted(known)},"permission":permission_by_agent[name]}
    tools=agents[primary_agent]["tools"]
    return {
        "tools": tools,
        "permission": permissions,
        "agent": agents,
    }


def _fuse_safety_overlay(primary_agent: str) -> dict[str, Any]:
    """Restrict CodeTalks to its staged skill, data root, and a read-only judge."""
    known = FUSE_SAFE_TOOLS | FORBIDDEN_TOOLS
    common = {"external_directory": "deny", "webfetch": "deny", "websearch": "deny", "browser": "deny",
              "patch": "deny", "shell": "deny", "question": "deny", "invalid": "deny", "todowrite": "deny"}
    primary = {**common, "read": "allow", "glob": "allow", "grep": "allow",
               "write": {"*": "deny", "codetalks-data/**": "allow"},
               "edit": {"*": "deny", "codetalks-data/**": "allow"},
               "skill": {"*": "deny", FUSE_SKILL: "allow"}, "task": {"*": "deny", "general": "allow"},
               "bash": {"*": "deny", "python* .opencode/skills/codetalks-source-driven-blackbox-v2/scripts/run_guard.py *": "allow",
                        "rg *": "allow", "find *": "allow", "ls *": "allow", "sed *": "allow"}}
    judge = {**common, "read": "allow", "glob": "allow", "grep": "allow", "write": "deny", "edit": "deny",
             "skill": {"*": "deny"}, "task": {"*": "deny"}, "bash": "deny"}
    tools = {name: name in FUSE_SAFE_TOOLS for name in sorted(known)}
    judge_tools = {name: name in EQUAL_TOOLS for name in sorted(known)}
    return {"tools": tools, "permission": primary,
            "agent": {primary_agent: {"tools": tools, "permission": primary},
                      "general": {"tools": judge_tools, "permission": judge}}}


def _required_workers(spec: RunSpec) -> list[str]:
    if spec.candidate == "fuse":
        return ["general"]
    task_text = spec.task.read_text(encoding="utf-8").casefold()
    workers = ["analysis-worker", "auditor"]
    if "mr-regression" in task_text or "/mr" in task_text:
        workers.append("mr-reader")
    return workers


def _read_regular_credential_source(path: Path, public_bundle: Path) -> bytes | None:
    """Read one bounded, stable regular source outside the candidate bundle."""
    if not path.is_absolute():
        return None
    try:
        path.resolve(strict=False).relative_to(public_bundle.resolve())
        return None
    except ValueError:
        pass
    try:
        before = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_size > _DEEPSEEK_CONFIG_MAX_BYTES:
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    or opened.st_size != before.st_size):
                return None
            raw = handle.read(_DEEPSEEK_CONFIG_MAX_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError:
        return None
    if (len(raw) != before.st_size or len(raw) > _DEEPSEEK_CONFIG_MAX_BYTES
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) !=
               (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)):
        return None
    return raw


def _read_deepseek_auth(inherited: Mapping[str, str], public_bundle: Path) -> str | None:
    """Return only the official DeepSeek key from a regular auth file."""
    candidates: list[Path] = []
    data_home = inherited.get("XDG_DATA_HOME")
    home = inherited.get("HOME")
    if isinstance(data_home, str) and data_home:
        candidates.append(Path(data_home) / "opencode" / "auth.json")
    if isinstance(home, str) and home:
        fallback = Path(home) / ".local" / "share" / "opencode" / "auth.json"
        if fallback not in candidates:
            candidates.append(fallback)
    bundle = public_bundle.resolve()
    for path in candidates:
        if not path.is_absolute():
            continue
        try:
            path.resolve(strict=False).relative_to(bundle)
            continue
        except ValueError:
            pass
        raw = _read_regular_credential_source(path, public_bundle)
        if raw is None:
            continue
        try:
            auth = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        entry = auth.get("deepseek") if isinstance(auth, dict) else None
        if (isinstance(entry, dict) and set(entry) == {"type", "key"} and entry.get("type") == "api"
                and isinstance(entry.get("key"), str) and entry["key"].strip()):
            return entry["key"]
    return None


def _read_deepseek_local_config(inherited: Mapping[str, str], public_bundle: Path) -> str | None:
    """Accept only the evaluator's frozen official DeepSeek OpenCode schema."""
    configured = inherited.get(DEEPSEEK_CONFIG_SOURCE_ENV)
    if isinstance(configured, str) and configured:
        path = Path(configured)
    else:
        home = inherited.get("HOME")
        if not isinstance(home, str) or not home:
            return None
        path = Path(home) / ".config" / "opencode" / "opencode.json"
    raw = _read_regular_credential_source(path, public_bundle)
    if raw is None:
        return None
    try:
        config = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(config, dict) or set(config) not in ({"model", "provider"}, {"$schema", "model", "provider"}):
        return None
    if config.get("$schema", OPENCODE_CONFIG_SCHEMA_URL) != OPENCODE_CONFIG_SCHEMA_URL or config.get("model") != DEEPSEEK_MODEL:
        return None
    provider = config.get("provider")
    if not isinstance(provider, dict) or set(provider) != {"deepseek"}:
        return None
    deepseek = provider.get("deepseek")
    expected_model = {"name": "DeepSeek V4 Flash", "limit": {"context": FROZEN_CONTEXT_WINDOW, "output": FROZEN_OUTPUT_LIMIT}}
    if not isinstance(deepseek, dict) or set(deepseek) != {"npm", "options", "models"}:
        return None
    if deepseek.get("npm") != "@ai-sdk/openai-compatible" or deepseek.get("models") != {"deepseek-v4-flash": expected_model}:
        return None
    options = deepseek.get("options")
    if not isinstance(options, dict) or set(options) != {"baseURL", "apiKey"}:
        return None
    key, base_url = options.get("apiKey"), options.get("baseURL")
    if (not isinstance(key, str) or not key.strip()
            or (key != "{env:DEEPSEEK_API_KEY}" and ("{" in key or "}" in key))
            or base_url not in {DEEPSEEK_OFFICIAL_BASE_URL, "{env:DEEPSEEK_BASE_URL}"}):
        return None
    return key


def deepseek_local_config_ready(inherited: Mapping[str, str], public_bundle: Path) -> bool:
    """Read-only readiness check for the approved local DeepSeek configuration."""
    return _read_deepseek_local_config(inherited, public_bundle) is not None


def _frozen_deepseek_provider_overlay() -> dict[str, Any]:
    """The only provider/model definition supplied to an isolated evaluator."""
    return {"model": DEEPSEEK_MODEL, "provider": {"deepseek": {
        "npm": "@ai-sdk/openai-compatible",
        "options": {"baseURL": DEEPSEEK_OFFICIAL_BASE_URL, "apiKey": "{env:DEEPSEEK_API_KEY}"},
        "models": {"deepseek-v4-flash": {"name": "DeepSeek V4 Flash",
                    "limit": {"context": FROZEN_CONTEXT_WINDOW, "output": FROZEN_OUTPUT_LIMIT}}},
    }}}


def _project_deepseek_credentials(
    inherited: Mapping[str, str], env: dict[str, str], data_root: Path, public_bundle: Path,
) -> bool:
    """Project one credential only into the temporary evaluator environment."""
    key = inherited.get("DEEPSEEK_API_KEY")
    if isinstance(key, str) and key.strip():
        env["DEEPSEEK_API_KEY"] = key
        return True
    env.pop("DEEPSEEK_API_KEY", None)
    key = _read_deepseek_auth(inherited, public_bundle)
    if key is None:
        key = _read_deepseek_local_config(inherited, public_bundle)
    # A source config may deliberately reference this evaluator-injected
    # variable.  It validates the provider definition but is not itself a
    # credential and must never be mistaken for one.
    if key is None or key == "{env:DEEPSEEK_API_KEY}":
        return False
    env["DEEPSEEK_API_KEY"] = key
    return True


def _execution_environment(
    spec: RunSpec,
    expected: dict[str, Any],
    agent: str,
    source: Mapping[str, str] | None,
    evaluator_root: Path,
    *,
    primary_task_enabled: bool = True,
    primary_phase: str | None = None,
    model_call_limit: int,
) -> tuple[dict[str, str], dict[str, Any], bool, dict[str, Any]]:
    inherited = os.environ if source is None else source
    env = {key: value for key, value in inherited.items() if key in ENVIRONMENT_ALLOWLIST}
    isolated = evaluator_root / "opencode-env"
    for name in ("home","config","data","cache"):
        (isolated/name).mkdir(parents=True,exist_ok=True,mode=0o700)
    tool_output = isolated / "tool-output"
    tool_output.mkdir(parents=True, exist_ok=True, mode=0o700)
    env.update({"HOME":str(isolated/"home"),"XDG_CONFIG_HOME":str(isolated/"config"),
                "XDG_DATA_HOME":str(isolated/"data"),"XDG_CACHE_HOME":str(isolated/"cache"),
                "TMPDIR":str(tool_output),"TMP":str(tool_output),"TEMP":str(tool_output)})
    env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
    env["DEEPSEEK_BASE_URL"] = DEEPSEEK_OFFICIAL_BASE_URL
    # Evaluator-owned keys replace, rather than merge with, caller-controlled
    # OpenCode config.  Receipts contain only key names and hashes.
    env["OPENCODE_EVALUATOR_POLICY"] = str(spec.isolated_policy)
    env["OPENCODE_EVALUATOR_CANDIDATE_NETWORK"] = str(expected["candidate_network"])
    env["OPENCODE_EVALUATOR_PROVIDER_TRANSPORT"] = str(expected["provider_transport"])
    override: dict[str, Any] | None = None
    if spec.track == "equal-tools":
        override = _equal_tools_override(agent)
    elif spec.track == "as-shipped":
        override = (_fuse_safety_overlay(agent) if spec.candidate == "fuse" else
                    _as_shipped_safety_overlay(agent, _required_workers(spec),
                                               primary_task_enabled=primary_task_enabled,
                                               primary_phase=primary_phase))
    config_overlay = _frozen_deepseek_provider_overlay()
    if override is not None:
        config_overlay.update(override)
    hook = _install_model_budget_hook(config_overlay, isolated, model_call_limit)
    _verified_model_budget_hook_uri(hook, isolated)
    env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] = "1"
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config_overlay, sort_keys=True, separators=(",", ":"))
    tokenized_overlay = {**config_overlay, "plugin": [_TOKENIZED_HOOK_URI]}
    receipt = {
        "environment_keys": sorted(env),
        "environment_values_recorded": False,
        "models_metadata_fetch_disabled": True,
        "opencode_debug_timeout_seconds": load_frozen_config()["runtime"]["opencode_debug_timeout_seconds"],
        "config_override_sha256": _canonical_hash(tokenized_overlay),
        "model_budget_hook_sha256": hook["plugin_sha256"],
        "model_call_limit": model_call_limit,
        "plugin_closure": _plugin_closure_receipt(hook),
    }
    provider_available = _project_deepseek_credentials(inherited, env, isolated / "data", spec.public_bundle)
    return env, receipt, provider_available, hook


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else "" if value is None else str(value)


_MODEL_BUDGET_BLOCK_ERROR = "PANGEA_EVALUATOR_MODEL_BUDGET_BLOCKED"
_TOKENIZED_HOOK_URI = "file://{ISOLATED_EVALUATOR_ROOT}/model-budget-hook/pre-request-budget.js"


def _install_model_budget_hook(
    config_overlay: dict[str, Any], environment_root: Path, model_call_limit: int,
) -> dict[str, Any]:
    """Bind one evaluator-private pre-request ``chat.params`` hook."""
    if type(model_call_limit) is not int or model_call_limit < 1:
        raise BenchmarkContractError("model-call hook requires a positive remaining budget")
    if "plugin" in config_overlay:
        raise BenchmarkContractError("evaluator model-call hook requires exclusive plugin closure")
    hook_root = environment_root / "model-budget-hook"
    hook_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    plugin_path = hook_root / "pre-request-budget.js"
    state_path = hook_root / "state.json"
    source = f'''import {{ writeFileSync }} from "node:fs";
import {{ fileURLToPath }} from "node:url";
const limit = {model_call_limit};
const statePath = fileURLToPath(new URL("./state.json", import.meta.url));
let admitted = 0;
function persist(blocked) {{
  writeFileSync(statePath, JSON.stringify({{
    schema_version: "1.0",
    model_call_limit: limit,
    model_requests_admitted: admitted,
    pre_request_budget_blocked: blocked
  }}) + "\\n", {{ encoding: "utf8", mode: 0o600 }});
}}
export default async function evaluatorModelBudgetPlugin() {{
  persist(false);
  return {{
    "chat.params": async function preRequestBudget() {{
      if (admitted >= limit) {{
        persist(true);
        throw new Error("{_MODEL_BUDGET_BLOCK_ERROR}");
      }}
      admitted += 1;
      persist(false);
    }}
  }};
}}
'''
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(plugin_path, flags, 0o400)
    with os.fdopen(descriptor, "wb") as handle:
        encoded = source.encode("utf-8")
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(plugin_path, 0o400)
    config_overlay["plugin"] = [plugin_path.resolve().as_uri()]
    return {
        "plugin_path": plugin_path,
        "state_path": state_path,
        "plugin_sha256": sha256(encoded).hexdigest(),
        "plugin_uri": plugin_path.resolve().as_uri(),
        "model_call_limit": model_call_limit,
    }


def _verified_model_budget_hook_uri(hook: Mapping[str, Any], isolated_root: Path) -> str:
    """Verify the evaluator-owned hook as one stable regular in-root file."""
    plugin_path = hook.get("plugin_path")
    expected_hash = hook.get("plugin_sha256")
    expected_uri = hook.get("plugin_uri")
    if (not isinstance(plugin_path, Path) or not isinstance(expected_hash, str)
            or not isinstance(expected_uri, str)):
        raise BenchmarkContractError("model budget hook binding is malformed")
    try:
        root_info = isolated_root.lstat()
        before = plugin_path.lstat()
        resolved_root = isolated_root.resolve(strict=True)
        resolved_plugin = plugin_path.resolve(strict=True)
        resolved_plugin.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise BenchmarkContractError("model budget hook is outside the isolated evaluator root") from exc
    if (stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode)
            or stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode)):
        raise BenchmarkContractError("model budget hook must be a regular non-symlink file")
    try:
        descriptor = os.open(plugin_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            payload = handle.read()
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise BenchmarkContractError("model budget hook cannot be read stably") from exc
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if (not stat.S_ISREG(opened.st_mode) or identity != opened_identity
            or opened_identity != after_identity or len(payload) != before.st_size
            or sha256(payload).hexdigest() != expected_hash
            or resolved_plugin.as_uri() != expected_uri):
        raise BenchmarkContractError("model budget hook content or identity differs from its binding")
    return expected_uri


def _plugin_closure_receipt(hook: Mapping[str, Any]) -> dict[str, Any]:
    """Return only tokenized/hash/count metadata; never persist a temporary root."""
    return {
        "plugin_uri": _TOKENIZED_HOOK_URI,
        "plugin_sha256": hook["plugin_sha256"],
        "plugin_count": 1,
        "plugin_array_sha256": _canonical_hash([_TOKENIZED_HOOK_URI]),
    }


def _resolved_plugin_closure(
    raw: str, hook: Mapping[str, Any], isolated_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    """Require the normal OpenCode resolved plugin array to be exactly the hook."""
    expected_uri = _verified_model_budget_hook_uri(hook, isolated_root)
    receipt = _plugin_closure_receipt(hook)
    try:
        resolved = json.loads(raw)
    except json.JSONDecodeError:
        return {**receipt, "parsed": False, "exact": False}, ["resolved_plugin_closure_invalid_json"]
    plugins = resolved.get("plugin") if isinstance(resolved, dict) else None
    exact = isinstance(plugins, list) and plugins == [expected_uri]
    safe_receipt = {
        **receipt,
        "parsed": isinstance(resolved, dict),
        "resolved_plugin_count": len(plugins) if isinstance(plugins, list) else None,
        "exact": exact,
    }
    return safe_receipt, [] if exact else ["resolved_plugin_closure_violation"]


def _model_budget_observation(
    hook: dict[str, Any], telemetry: dict[str, Any], *, injected_runner: bool,
) -> dict[str, Any]:
    """Read only finite counters from the evaluator-private hook state."""
    limit = hook["model_call_limit"]
    completed = telemetry["model_calls"]
    if injected_runner:
        admitted = completed
        blocked = completed > limit
        enforced = False
    else:
        state_path = hook["state_path"]
        try:
            state = _load_json(state_path)
        except BenchmarkContractError:
            state = {}
        expected = {"schema_version", "model_call_limit", "model_requests_admitted", "pre_request_budget_blocked"}
        valid = (
            set(state) == expected and state.get("schema_version") == "1.0"
            and state.get("model_call_limit") == limit
            and type(state.get("model_requests_admitted")) is int
            and 0 <= state["model_requests_admitted"] <= limit
            and type(state.get("pre_request_budget_blocked")) is bool
        )
        if not valid:
            admitted = 0
            blocked = False
            enforced = False
        else:
            admitted = state["model_requests_admitted"]
            blocked = state["pre_request_budget_blocked"]
            enforced = True
    return {
        "model_call_limit": limit,
        "model_calls_completed": completed,
        "model_requests_admitted": admitted,
        "pre_request_budget_blocked": blocked,
        "pre_request_budget_enforced": enforced,
        "injected_test_runner": injected_runner,
    }


def _empty_receipt(
    spec: RunSpec,
    command: list[str],
    failures: list[str],
    *,
    duration: float = 0.0,
    exit_code: int = -1,
    stdout: str = "",
    stderr: str = "",
    environment: dict[str, Any] | None = None,
    preflight: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> RunReceipt:
    telemetry = parse_jsonl_telemetry(stdout.splitlines(True))
    return RunReceipt(
        candidate=spec.candidate,
        track=spec.track,
        case_id=spec.case_id,
        command=command,
        exit_code=exit_code,
        duration_seconds=duration,
        stdout_sha256=sha256(stdout.encode()).hexdigest(),
        stderr_sha256=sha256(stderr.encode()).hexdigest(),
        telemetry=telemetry,
        environment_keys=list((environment or {}).get("environment_keys", [])),
        preflight=preflight or {},
        policy_receipt=policy or {},
        passed=False,
        failures=list(dict.fromkeys(failures)),
    )


def _permission_decision(rules: list[dict[str, Any]], permission: str, target: str) -> str | None:
    decision: str | None = None
    for rule in rules:
        if rule.get("permission") in {permission, "*"} and fnmatch.fnmatchcase(target, str(rule.get("pattern", "*"))):
            decision = rule.get("action") if rule.get("action") in {"allow", "deny", "ask"} else None
    return decision


def _normalized_permission_rules(value: Any) -> list[tuple[str, str, str]] | None:
    if not isinstance(value, list):
        return None
    rows: list[tuple[str, str, str]] = []
    for rule in value:
        if (not isinstance(rule, dict) or set(rule) != {"permission", "pattern", "action"}
                or not isinstance(rule["permission"], str) or not isinstance(rule["pattern"], str)
                or rule["action"] not in {"allow", "deny", "ask"}):
            return None
        rows.append((rule["permission"], rule["pattern"], rule["action"]))
    return rows


_OPENCODE_1184_PERMISSION_DEFAULT_ROWS = (
    ("*", "*", "allow"),
    ("doom_loop", "*", "ask"),
    ("external_directory", "*", "ask"),
    ("question", "*", "deny"),
    ("plan_enter", "*", "deny"),
    ("plan_exit", "*", "deny"),
    ("read", "*", "allow"),
    ("read", "*.env", "ask"),
    ("read", "*.env.*", "ask"),
    ("read", "*.env.example", "allow"),
)
_OPENCODE_1184_PERMISSION_DEFAULTS = Counter(_OPENCODE_1184_PERMISSION_DEFAULT_ROWS)


def _permission_projection(
    rows: Iterable[tuple[str, str, str]], permission: str,
) -> list[tuple[str, str, str]]:
    return [row for row in rows if row[0] in {"*", permission}]


def _same_action_blocks_match(
    actual: list[tuple[str, str, str]], expected: list[tuple[str, str, str]],
) -> bool:
    """Allow order changes only inside a contiguous, decision-equivalent action block."""
    def blocks(rows: list[tuple[str, str, str]]) -> list[tuple[str, Counter[tuple[str, str, str]]]]:
        grouped: list[tuple[str, Counter[tuple[str, str, str]]]] = []
        for row in rows:
            if not grouped or grouped[-1][0] != row[2]:
                grouped.append((row[2], Counter()))
            grouped[-1][1][row] += 1
        return grouped
    return blocks(actual) == blocks(expected)


def _exact_intended_permission_order(
    actual: list[tuple[str, str, str]], intended: dict[str, Any],
) -> bool:
    """Ignore cross-permission interleaving, but preserve every decision order."""
    ordered: list[tuple[str, str, str]] = []
    for permission, rule in intended.items():
        if isinstance(rule, str):
            ordered.append((permission, "*", rule))
        elif isinstance(rule, dict):
            ordered.extend((permission, pattern, action) for pattern, action in rule.items())
        else:
            return False
    permissions = {row[0] for row in ordered}
    return all(
        _same_action_blocks_match(
            _permission_projection(actual, permission), _permission_projection(ordered, permission),
        )
        for permission in permissions
    )


def _opencode_1184_permission_order(
    actual: list[tuple[str, str, str]], intended_rows: Counter[tuple[str, str, str]], *,
    data_pattern: str, temp_pattern: str, immutable_skill_patterns: Iterable[str],
    global_rows: Counter[tuple[str, str, str]] | None,
) -> bool:
    """Validate the frozen ordered projections that can affect final decisions.

    OpenCode interleaves different permission names and discovers immutable
    skill directories in filesystem order.  Neither changes a decision.  For
    each individual permission, however, the built-in, global, agent, and
    trailing XDG blocks have a frozen order and must not be permuted.
    """
    if not actual or actual[0] != ("*", "*", "allow"):
        return False
    global_rows = global_rows or Counter()
    permissions = {row[0] for row in actual if row[0] != "*"}
    defaults = list(_OPENCODE_1184_PERMISSION_DEFAULT_ROWS)
    for permission in permissions - {"external_directory"}:
        expected = _permission_projection(defaults, permission)
        expected.extend(sorted(row for row in global_rows.elements() if row[0] == permission))
        expected.extend(sorted(row for row in intended_rows.elements() if row[0] == permission))
        if not _same_action_blocks_match(_permission_projection(actual, permission), expected):
            return False

    external = _permission_projection(actual, "external_directory")
    early_generated = Counter({
        ("external_directory", data_pattern, "allow"): 1,
        ("external_directory", temp_pattern, "allow"): 1,
        **{("external_directory", pattern, "allow"): 1 for pattern in immutable_skill_patterns},
    })
    prefix = [("*", "*", "allow"), ("external_directory", "*", "ask")]
    suffix = [
        *sorted(row for row in global_rows.elements() if row[0] == "external_directory"),
        *sorted(row for row in intended_rows.elements() if row[0] == "external_directory"),
        ("external_directory", data_pattern, "allow"),
    ]
    middle_end = len(external) - len(suffix)
    return (
        middle_end >= len(prefix)
        and external[:len(prefix)] == prefix
        and Counter(external[len(prefix):middle_end]) == early_generated
        and external[middle_end:] == suffix
    )


def _manifest_contains_frozen_skill(bundle: Path, skill: str) -> bool:
    try:
        manifest = _load_json(bundle / "public-bundle-manifest.json")
    except (OSError, json.JSONDecodeError, BenchmarkContractError):
        return False
    files = manifest.get("files")
    prefix = f".opencode/skills/{skill}/"
    source = ROOT / ".opencode" / "skills" / skill
    expected = {
        prefix + path.relative_to(source).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*") if path.is_file() and not path.is_symlink()
    }
    observed = ({name: digest for name, digest in files.items() if isinstance(name, str) and name.startswith(prefix)}
                if isinstance(files, dict) else {})
    return bool(expected) and observed == expected


def _manifest_contains_frozen_agent(bundle: Path, agent: str) -> bool:
    try:
        manifest = _load_json(bundle / "public-bundle-manifest.json")
        files = manifest.get("files")
        relative = f".opencode/agents/{agent}.md"
        source = ROOT / relative
        return (isinstance(files, dict) and source.is_file() and not source.is_symlink()
                and files.get(relative) == sha256(source.read_bytes()).hexdigest())
    except (OSError, json.JSONDecodeError, BenchmarkContractError):
        return False


def _known_resolved_permission_rules(
    permissions: Any,
    intended: dict[str, Any],
    *,
    agent: str,
    isolated_root: Path | None,
    public_bundle: Path | None,
    global_intended: dict[str, Any] | None,
) -> tuple[bool, dict[str, int]]:
    """Accept only the deterministic OpenCode 1.18.4 projection.

    Unit runners may still return the exact intended overlay.  A real debug
    projection additionally contains the fixed OpenCode defaults, two XDG
    tool-output entries, one TMPDIR entry, and (for the primary public bundle)
    one read-only discovery entry per frozen skill.  No other rule is accepted.
    """
    normalized = _normalized_permission_rules(permissions)
    if normalized is None:
        return False, {}
    actual = Counter(normalized)
    intended_rows = Counter(_permission_config_rules(intended))
    if actual == intended_rows:
        ordered = _exact_intended_permission_order(normalized, intended)
        return (ordered, {"intended": sum(intended_rows.values())} if ordered else {})
    if isolated_root is None:
        return False, {}
    expected = intended_rows + _OPENCODE_1184_PERMISSION_DEFAULTS
    classes = {
        "intended": sum(intended_rows.values()),
        "opencode_1_18_4_defaults": sum(_OPENCODE_1184_PERMISSION_DEFAULTS.values()),
        "isolated_tool_output": 3,
        "immutable_bundle_skill": 0,
    }
    data_pattern = str(isolated_root / "data" / "opencode" / "tool-output" / "*")
    temp_pattern = str(isolated_root / "tool-output" / "opencode" / "*")
    expected[("external_directory", data_pattern, "allow")] += 2
    expected[("external_directory", temp_pattern, "allow")] += 1
    global_rows: Counter[tuple[str, str, str]] | None = None
    if global_intended is not None:
        global_rows = Counter(_permission_config_rules(global_intended))
        expected += global_rows
        classes["global_overlay"] = sum(global_rows.values())
    immutable_skill_patterns: list[str] = []
    if public_bundle is not None:
        if not _manifest_contains_frozen_agent(public_bundle, agent):
            return False, {}
        for skill in sorted(AS_SHIPPED_SKILLS):
            if not _manifest_contains_frozen_skill(public_bundle, skill):
                return False, {}
            pattern = str(public_bundle.resolve() / ".opencode" / "skills" / skill / "*")
            immutable_skill_patterns.append(pattern)
            expected[("external_directory", pattern, "allow")] += 1
            classes["immutable_bundle_skill"] += 1
    closed = actual == expected and _opencode_1184_permission_order(
        normalized, intended_rows, data_pattern=data_pattern, temp_pattern=temp_pattern,
        immutable_skill_patterns=immutable_skill_patterns, global_rows=global_rows,
    )
    return closed, classes if closed else {}


def _receipt_path_token(pattern: str, isolated_root: Path | None, public_bundle: Path | None) -> str:
    path = Path(pattern)
    if not path.is_absolute():
        return pattern
    for label, root in (("ISOLATED", isolated_root), ("PUBLIC_BUNDLE", public_bundle)):
        if root is None:
            continue
        try:
            relative = path.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        return f"{{{label}}}/{relative}"
    return "{EXTERNAL_ABSOLUTE_PATH}"


def _resolved_receipt_hashes(
    resolved: dict[str, Any], isolated_root: Path | None, public_bundle: Path | None,
) -> tuple[str, str]:
    sanitized = dict(resolved)
    rules = resolved.get("permission")
    normalized_rules: list[dict[str, str]] = []
    if isinstance(rules, list):
        for rule in rules:
            if isinstance(rule, dict):
                normalized_rules.append({
                    "permission": str(rule.get("permission", "")),
                    "pattern": _receipt_path_token(str(rule.get("pattern", "")), isolated_root, public_bundle),
                    "action": str(rule.get("action", "")),
                })
    sanitized["permission"] = normalized_rules
    return _canonical_hash(sanitized), _canonical_hash(normalized_rules)


def _permission_config_rules(value: dict[str, Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for permission, rule in value.items():
        if isinstance(rule, str):
            rows.append((permission, "*", rule))
        elif isinstance(rule, dict):
            rows.extend((permission, pattern, action) for pattern, action in rule.items())
        else:
            raise BenchmarkContractError("invalid intended permission overlay")
    return sorted(rows)


def _baseline_agent_receipt(
    raw: str, agent: str, *, worker: bool, isolated_root: Path | None = None,
    public_bundle: Path | None = None,
) -> dict[str, Any] | None:
    try:
        resolved = json.loads(raw)
    except json.JSONDecodeError:
        return None
    expected_modes = {"subagent", "hidden"} if worker else {"primary", "all"}
    if (
        not isinstance(resolved, dict) or resolved.get("name") != agent
        or resolved.get("mode") not in expected_modes
        or not isinstance(resolved.get("prompt"), str) or not resolved["prompt"].strip()
    ):
        return None
    resolved_hash, _ = _resolved_receipt_hashes(resolved, isolated_root, public_bundle)
    return {
        "agent": agent,
        "resolved_config_sha256": resolved_hash,
        "prompt_sha256": sha256(str(resolved.get("prompt", "")).encode()).hexdigest(),
    }


def _resolved_agent_receipt(
    raw: str, agent: str, track: str, expected: dict[str, Any], *, worker: bool = False, candidate: str = "pangea",
    primary_task_enabled: bool = True, primary_phase: str | None = None, isolated_root: Path | None = None,
    public_bundle: Path | None = None, global_permission: dict[str, Any] | None = None,
    tool_free: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    try:
        resolved = json.loads(raw)
    except json.JSONDecodeError:
        return {"parsed": False}, ["agent_preflight_invalid_json"]
    expected_modes = {"subagent", "hidden"} if worker else {"primary", "all"}
    if (
        not isinstance(resolved, dict) or resolved.get("name") != agent
        or resolved.get("mode") not in expected_modes
        or not isinstance(resolved.get("prompt"), str) or not resolved["prompt"].strip()
    ):
        return {"parsed": False}, ["agent_preflight_invalid_config"]
    tools = resolved.get("tools")
    if not isinstance(tools, dict) or not all(isinstance(key, str) and isinstance(value, bool) for key, value in tools.items()):
        return {"parsed": False}, ["agent_preflight_invalid_tools"]
    enabled = {key for key, value in tools.items() if value}
    if track == "equal-tools":
        if enabled != EQUAL_TOOLS:
            failures.append("resolved_tool_policy_violation")
        permissions = resolved.get("permission")
        intended = _equal_tools_override(agent)["agent"][agent]["permission"]
        permission_ok, permission_classes = _known_resolved_permission_rules(
            permissions, intended, agent=agent, isolated_root=isolated_root,
            public_bundle=public_bundle if candidate == "pangea" else None,
            global_intended=global_permission,
        )
        if not permission_ok:
            failures.append("resolved_overlay_permission_violation")
    else:
        if candidate == "fuse":
            allowed = FUSE_SAFE_TOOLS if not worker else EQUAL_TOOLS
            if enabled != allowed or enabled & NETWORK_TOOL_NAMES:
                failures.append("resolved_tool_policy_violation")
            permissions = resolved.get("permission")
            intended = _fuse_safety_overlay(FUSE_AGENT)["agent"]["general" if worker else FUSE_AGENT]["permission"]
            permission_ok, permission_classes = _known_resolved_permission_rules(
                permissions, intended, agent=agent, isolated_root=isolated_root,
                public_bundle=None,
                global_intended=global_permission,
            )
            if not permission_ok:
                failures.append("resolved_overlay_permission_violation")
            resolved_hash, permission_hash = _resolved_receipt_hashes(resolved, isolated_root, public_bundle)
            return {"parsed": True, "agent": agent, "enabled_tools": sorted(enabled),
                    "resolved_config_sha256": resolved_hash,
                    "permission_rules_sha256": permission_hash,
                    "permission_rule_classes": permission_classes,
                    "prompt_sha256": sha256(str(resolved.get("prompt", "")).encode()).hexdigest()}, failures
        role="primary" if not worker else agent
        allowed = set() if tool_free else set(AS_SHIPPED_ROLE_TOOLS.get(role,frozenset()))
        if role == "primary" and primary_phase == "intake":
            allowed = {"bash"}
        elif role == "primary" and not primary_task_enabled:
            allowed.discard("task")
        permissions = resolved.get("permission")
        if not isinstance(permissions, list) or not all(isinstance(rule, dict) for rule in permissions):
            failures.append("resolved_overlay_permission_violation")
            permissions = []
        intended = ({"*":"deny"} if tool_free else _as_shipped_safety_overlay(
            agent if role == "primary" else "pangea-test",
            [] if role == "primary" else [agent],
            primary_task_enabled=primary_task_enabled if role == "primary" else True,
            primary_phase=primary_phase if role == "primary" else None,
        )["agent"][agent]["permission"])
        permission_ok, permission_classes = _known_resolved_permission_rules(
            permissions, intended, agent=agent, isolated_root=isolated_root,
            public_bundle=public_bundle if candidate == "pangea" else None,
            global_intended=global_permission,
        )
        if not permission_ok:
            failures.append("resolved_overlay_permission_violation")
        task_deny_targets = [*expected["task_allowlist"], "unknown-worker"]
        task_deny_verified = (
            role == "primary" and not primary_task_enabled and permission_ok
            and all(_permission_decision(permissions, "task", target) == "deny"
                    for target in task_deny_targets)
        )
        # OpenCode 1.18.4 can keep Task registered in debug output even when
        # the intake overlay disables it.  Project it away only after both the
        # complete ordered permission closure and every Task target deny have
        # succeeded; a failed receipt must keep the registered tool visible.
        registered_but_denied = {"task"} if task_deny_verified else set()
        effective_enabled = enabled - registered_but_denied
        forbidden = set(expected["forbidden_enabled_tools"])
        if effective_enabled != allowed or effective_enabled & forbidden:
            failures.append("resolved_tool_policy_violation")
        if tool_free:
            checks=[("read","*","deny"),("glob","*","deny"),("grep","*","deny"),
                    ("task","analysis-worker","deny"),("skill","storage-spdk","deny"),
                    ("bash","git status --short","deny")]
        elif role == "primary" and primary_phase == "intake":
            checks = [("bash", EVALUATOR_INTAKE_COMMAND, "allow"),
                      ("bash", " " + EVALUATOR_INTAKE_COMMAND, "deny"),
                      ("bash", EVALUATOR_INTAKE_COMMAND + " ", "deny"),
                      ("read", "TASK.md", "deny"), ("glob", "*", "deny"),
                      ("grep", "CASE", "deny"), ("task", "analysis-worker", "deny"),
                      ("skill", "storage-spdk", "deny")]
        elif role=="primary":
            checks=[*[("task",target,"allow" if primary_task_enabled else "deny") for target in expected["task_allowlist"]],
                    *[("skill",name,"allow") for name in expected["skill_allowlist"]],
                    ("bash","git status --short","allow"),("bash","python3 runtime/runctl.py status","allow")]
        elif role=="mr-reader":
            checks=[("task","analysis-worker","deny"),("skill","storage-spdk","deny"),
                    ("bash","git status --short","allow"),("bash","python3 runtime/runctl.py status","deny")]
        else:
            checks=[("task","analysis-worker","deny"),("task","auditor","deny"),("skill","storage-spdk","deny"),
                    ("bash","git status --short","deny"),("bash","python3 runtime/runctl.py status","deny")]
        checks += [("task","unknown-worker","deny"),("skill","unknown-skill","deny"),
                   ("bash","curl https://example.invalid","deny"),("bash","git reset --hard","deny"),
                   ("external_directory","*","deny"),
                   *[(name,"*","deny") for name in expected["forbidden_enabled_tools"]]]
        if any(_permission_decision(permissions, permission, target) != action for permission, target, action in checks):
            failures.append("resolved_overlay_permission_violation")
        enabled = effective_enabled
    resolved_hash, permission_hash = _resolved_receipt_hashes(resolved, isolated_root, public_bundle)
    return {
        "parsed": True,
        "agent": agent,
        "enabled_tools": sorted(enabled),
        "resolved_config_sha256": resolved_hash,
        "permission_rules_sha256": permission_hash,
        "permission_rule_classes": permission_classes,
        "prompt_sha256": sha256(str(resolved.get("prompt", "")).encode()).hexdigest(),
    }, failures


def _bundle_integrity_snapshot(bundle: Path, managed_roots: Iterable[str]) -> dict[str, Any]:
    manifest_path = bundle / "public-bundle-manifest.json"
    manifest = _load_json(manifest_path)
    managed = set(managed_roots)
    failures: list[str] = []
    files: dict[str, str] = {}
    entries: set[str] = set()
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle).as_posix()
        entries.add(relative)
        try:
            mode = path.lstat().st_mode
        except OSError:
            failures.append("bundle_entry_uninspectable")
            continue
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            failures.append("bundle_symlink_or_special_file")
            continue
        if path.is_file():
            files[relative] = sha256(path.read_bytes()).hexdigest()
    return {
        "files": files,
        "entries": sorted(entries),
        "manifest_files": manifest.get("files", {}),
        "manifest_sha256": files.get("public-bundle-manifest.json"),
        "managed_roots": sorted(managed),
        "inspection_failures": sorted(set(failures)),
    }


def _compare_bundle_integrity(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    failures = list(after["inspection_failures"])
    managed = set(before["managed_roots"])
    def is_managed(relative: str) -> bool:
        return bool(Path(relative).parts and Path(relative).parts[0] in managed)
    baseline = before["files"]
    observed = after["files"]
    baseline_entries = set(before["entries"])
    observed_entries = set(after["entries"])
    protected = {path for path in baseline if not is_managed(path)}
    changed = {path for path in protected if observed.get(path) != baseline[path]}
    deleted_entries = {path for path in baseline_entries - observed_entries if not is_managed(path)}
    added = {path for path in observed_entries - baseline_entries if not is_managed(path)}
    changed.update(deleted_entries)
    if changed:
        failures.append("protected_bundle_file_changed_or_deleted")
    if added:
        failures.append("out_of_scope_bundle_file_added")
    receipt = {
        "before_manifest_sha256": before["manifest_sha256"],
        "after_manifest_sha256": after["manifest_sha256"],
        "managed_write_roots": before["managed_roots"],
        "protected_changed_count": len(changed),
        "out_of_scope_added_count": len(added),
        "managed_file_delta_count": len({path for path in set(baseline) | set(observed) if is_managed(path) and baseline.get(path) != observed.get(path)}),
        "passed": not failures,
    }
    return receipt, list(dict.fromkeys(failures))


def _capture_validated_public_bundle_binding(
    bundle: Path, managed_root: str = "pangea-data",
) -> _ValidatedPublicBundleBinding:
    """Bind a bundle immediately after the evaluator's full entry validation."""
    if managed_root not in MANAGED_WRITE_ROOTS:
        raise BenchmarkContractError("public bundle binding requires the frozen managed root")
    snapshot = _bundle_integrity_snapshot(bundle, [managed_root])
    if snapshot["inspection_failures"]:
        raise BenchmarkContractError("validated public bundle snapshot is not regular")
    return _ValidatedPublicBundleBinding(
        bundle.resolve(), snapshot, managed_root, _PUBLIC_BUNDLE_BINDING_AUTHORITY,
    )


def _validate_bound_public_bundle(
    bundle: Path, binding: _ValidatedPublicBundleBinding,
) -> None:
    """Revalidate immutable entries while leaving managed Run contents to their closures."""
    if (not isinstance(binding, _ValidatedPublicBundleBinding)
            or binding.authority is not _PUBLIC_BUNDLE_BINDING_AUTHORITY
            or binding.managed_root not in MANAGED_WRITE_ROOTS
            or bundle.resolve() != binding.root):
        raise BenchmarkContractError("invalid evaluator-owned public bundle binding")
    current = _bundle_integrity_snapshot(bundle, [binding.managed_root])
    _, failures = _compare_bundle_integrity(binding.snapshot, current)
    plugin_entries = _opencode_project_plugin_entries(bundle)
    if plugin_entries:
        failures.append("OpenCode project plugin entry added after initial validation")
    if failures:
        raise BenchmarkContractError(
            "bound public bundle integrity failed: " + "; ".join(dict.fromkeys(failures))
        )


def _immutable_public_bundle_binding(bundle: Path) -> dict[str, Any]:
    """Return the exact immutable candidate/source portion of a public bundle.

    Runs, reports, contracts, indexes, and inbox state are evaluator-managed
    runtime state.  Frozen source/library/registry inputs remain immutable even
    though they live below the managed ``pangea-data`` mount.
    """
    bundle = Path(bundle).resolve()
    snapshot = _bundle_integrity_snapshot(bundle, ["pangea-data"])
    if snapshot["inspection_failures"]:
        raise BenchmarkContractError("immutable public bundle snapshot is not regular")
    immutable_managed = {"repositories", "library", "registry"}

    def immutable(relative: str) -> bool:
        parts = Path(relative).parts
        return bool(parts) and (parts[0] != "pangea-data"
                                or (len(parts) > 1 and parts[1] in immutable_managed))

    entries = sorted(relative for relative in snapshot["entries"] if immutable(relative))
    files = [{"path": relative, "sha256": snapshot["files"][relative]}
             for relative in sorted(snapshot["files"]) if immutable(relative)]
    return {
        "artifact_type": "immutable_public_bundle_binding",
        "schema_version": "1.0",
        "managed_root": "pangea-data",
        "immutable_managed_subroots": sorted(immutable_managed),
        "entries": entries,
        "files": files,
    }


def execute_opencode(
    spec: RunSpec,
    *,
    run=subprocess.run,
    environ: Mapping[str, str] | None = None,
    model_call_limit: int | None = None,
) -> RunReceipt:
    """Execute one sealed run inside evaluator-owned temporary storage."""
    with tempfile.TemporaryDirectory(prefix="pangea-evaluator-") as temporary:
        return _execute_opencode_in_root(
            spec, Path(temporary), run=run, environ=environ,
            model_call_limit=model_call_limit,
        )


def _validate_runspec_case_binding(
    spec: RunSpec, binding: _ValidatedPublicBundleBinding,
) -> None:
    """Reverify the fixed case files without walking evaluator-managed state."""
    if (not isinstance(binding, _ValidatedPublicBundleBinding)
            or binding.authority is not _PUBLIC_BUNDLE_BINDING_AUTHORITY
            or binding.managed_root != ("pangea-data" if spec.candidate == "pangea" else "codetalks-data")
            or spec.public_bundle.resolve() != binding.root):
        raise BenchmarkContractError("RunSpec case binding requires a validated public bundle binding")
    if spec.public_case_path != "CASE.json" or not re.fullmatch(r"[0-9a-f]{64}", spec.public_case_sha256):
        raise BenchmarkContractError("RunSpec lacks an exact public-case binding")
    expected_task = spec.public_bundle / "TASK.md"
    if spec.task != expected_task:
        raise BenchmarkContractError("RunSpec task path differs from the staged canonical task")
    case_path = spec.public_bundle / spec.public_case_path
    case_payload = _stable_regular_file_bytes(case_path, "RunSpec public case", read_only=True)
    if sha256(case_payload).hexdigest() != spec.public_case_sha256:
        raise BenchmarkContractError("RunSpec public case hash differs from staged case")
    try:
        case = json.loads(case_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise BenchmarkContractError("RunSpec public case is invalid JSON") from exc
    if not isinstance(case, dict):
        raise BenchmarkContractError("RunSpec public case must be an object")
    if case.get("id") != spec.case_id:
        raise BenchmarkContractError("RunSpec case id differs from staged case")

    # The workspace manifest and its schema are the frozen authority.  Parsed
    # equality alone is insufficient because CASE.json has one canonical byte
    # representation shared by PANGEA and Fuse.
    from benchmarks import stage as public_stage
    try:
        manifest = public_stage.load_manifest()
        manifest_errors = public_stage._validate_manifest_snapshot(manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise BenchmarkContractError("frozen public-case manifest is unavailable") from exc
    if manifest_errors:
        raise BenchmarkContractError("frozen public-case manifest or schema is invalid")
    canonical = next((item for item in manifest["cases"] if item["id"] == spec.case_id), None)
    if canonical is None or case != canonical or case_payload != public_stage.canonical_case_payload(canonical):
        raise BenchmarkContractError("RunSpec public case differs from the exact frozen manifest member")

    task_payload = _stable_regular_file_bytes(expected_task, "RunSpec task")
    try:
        task_text = task_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BenchmarkContractError("RunSpec task is not UTF-8") from exc
    if (not task_text.strip() or "\x00" in task_text or "\r" in task_text
            or task_text.splitlines().count(canonical["agent_input"]) != 1):
        raise BenchmarkContractError("RunSpec task does not bind exactly one canonical agent_input line")

    receipt_path = spec.public_bundle / "stage-receipt.json"
    receipt_payload = _stable_regular_file_bytes(receipt_path, "stage receipt")
    bundle_manifest_path = spec.public_bundle / "public-bundle-manifest.json"
    bundle_manifest_payload = _stable_regular_file_bytes(bundle_manifest_path, "public bundle manifest")
    try:
        receipt = json.loads(receipt_payload.decode("utf-8"))
        bundle_manifest = json.loads(bundle_manifest_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise BenchmarkContractError("public case binding metadata is invalid JSON") from exc
    contract_hash = sha256(json.dumps(
        canonical["contract"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    expected_receipt = {
        "schema_version": "1.0",
        "candidate": spec.candidate,
        "task_hash": sha256(task_payload).hexdigest(),
        "task_binding_version": "canonical-agent-input-line-v1",
        "agent_input_sha256": sha256(canonical["agent_input"].encode("utf-8")).hexdigest(),
        "case_path": "CASE.json",
        "case_id": spec.case_id,
        "case_sha256": spec.public_case_sha256,
        "contract_projection_sha256": contract_hash,
    }
    _validate_stage_receipt(
        receipt, root=spec.public_bundle, expected=expected_receipt,
        candidate_manifest_sha256=spec.candidate_manifest_sha256,
    )
    if any(receipt[key] != value for key, value in expected_receipt.items()):
        raise BenchmarkContractError("RunSpec public case differs from the stage receipt")
    if receipt.get("candidate_manifest_sha256") != spec.candidate_manifest_sha256:
        raise BenchmarkContractError("RunSpec candidate manifest differs from the stage receipt")
    if spec.candidate == "pangea":
        if spec.candidate_manifest_sha256 is not None:
            raise BenchmarkContractError("PANGEA must not bind a CodeTalks candidate manifest")
    elif spec.candidate == "fuse":
        digest = spec.candidate_manifest_sha256
        candidate_files = receipt.get("candidate_files")
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(candidate_files, dict)
                or candidate_files.get("codetalks-evaluator-manifest.json") != digest):
            raise BenchmarkContractError("Fuse candidate manifest binding is invalid")
    files = bundle_manifest.get("files") if isinstance(bundle_manifest, dict) else None
    initial_files = binding.snapshot.get("files")
    initial_manifest_files = binding.snapshot.get("manifest_files")
    fixed_hashes = {
        "CASE.json": spec.public_case_sha256,
        "TASK.md": sha256(task_payload).hexdigest(),
        "stage-receipt.json": sha256(receipt_payload).hexdigest(),
    }
    if (not isinstance(files, dict)
            or not isinstance(initial_files, dict)
            or not isinstance(initial_manifest_files, dict)
            or sha256(bundle_manifest_payload).hexdigest()
               != initial_files.get("public-bundle-manifest.json")
            or any(files.get(path) != digest for path, digest in fixed_hashes.items())
            or any(initial_files.get(path) != digest for path, digest in fixed_hashes.items())
            or any(initial_manifest_files.get(path) != digest for path, digest in fixed_hashes.items())):
        raise BenchmarkContractError("RunSpec public case differs from the public bundle manifest")


def _validate_stage_receipt(
    receipt: Any, *, root: Path | None = None, expected: Mapping[str, Any] | None = None,
    candidate_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the exact authoritative public staging receipt schema."""
    digest = re.compile(r"[0-9a-f]{64}")
    commit = re.compile(r"[0-9a-f]{40}")
    top = {"schema_version", "repositories", "candidate", "candidate_files",
           "candidate_directories", "candidate_tree_sha256", "candidate_manifest_sha256",
           "task_hash", "task_binding_version", "agent_input_sha256", "case_path", "case_id",
           "case_sha256", "contract_projection_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != top or receipt.get("schema_version") != "1.0":
        raise BenchmarkContractError("stage receipt top-level closure is invalid")
    candidate = receipt.get("candidate")
    files = receipt.get("candidate_files"); directories = receipt.get("candidate_directories")
    if (candidate not in {"pangea", "fuse"} or not isinstance(files, dict)
            or list(files) != sorted(files) or any(
                not isinstance(path, str) or not path or Path(path).is_absolute()
                or ".." in Path(path).parts or Path(path).as_posix() != path
                or not isinstance(value, str) or not digest.fullmatch(value)
                for path, value in files.items())
            or not isinstance(directories, list) or directories != sorted(directories)
            or len(set(directories)) != len(directories)
            or any(not isinstance(path, str) or not path or Path(path).is_absolute()
                   or ".." in Path(path).parts or Path(path).as_posix() != path
                   for path in directories)
            or not digest.fullmatch(str(receipt.get("candidate_tree_sha256", "")))
            or any(not digest.fullmatch(str(receipt.get(key, ""))) for key in
                   ("task_hash", "agent_input_sha256", "case_sha256", "contract_projection_sha256"))
            or receipt.get("task_binding_version") != "canonical-agent-input-line-v1"
            or receipt.get("case_path") != "CASE.json"
            or not isinstance(receipt.get("case_id"), str) or not receipt["case_id"]):
        raise BenchmarkContractError("stage receipt candidate/case fields are invalid")
    manifest_hash = receipt.get("candidate_manifest_sha256")
    if ((candidate == "pangea" and (manifest_hash is not None
                                    or "codetalks-evaluator-manifest.json" in files))
            or (candidate == "fuse" and (not isinstance(manifest_hash, str)
                                         or not digest.fullmatch(manifest_hash)
                                         or files.get("codetalks-evaluator-manifest.json") != manifest_hash))
            or manifest_hash != candidate_manifest_sha256):
        raise BenchmarkContractError("stage receipt candidate branch is invalid")
    repositories = receipt.get("repositories")
    row_keys = {"id", "commit", "git_tree", "materialization_version", "materialization_sha256",
                "entry_count", "entry_counts", "materialized_symlinks", "materialized_gitlinks",
                "executable_files"}
    if (not isinstance(repositories, list)
            or [row.get("id") if isinstance(row, dict) else None for row in repositories]
               != ["spdk", "nvme-cli"]):
        raise BenchmarkContractError("stage receipt repository order is invalid")
    for row in repositories:
        counts = row.get("entry_counts") if isinstance(row, dict) else None
        if (not isinstance(row, dict) or set(row) != row_keys
                or not commit.fullmatch(str(row.get("commit", "")))
                or not commit.fullmatch(str(row.get("git_tree", "")))
                or row.get("materialization_version") != "git-object-v1"
                or not digest.fullmatch(str(row.get("materialization_sha256", "")))
                or type(row.get("entry_count")) is not int or row["entry_count"] < 0
                or not isinstance(counts, dict)
                or set(counts) != {"regular", "executable", "symlink", "gitlink"}
                or any(type(value) is not int or value < 0 for value in counts.values())
                or sum(counts.values()) != row["entry_count"]):
            raise BenchmarkContractError("stage receipt repository row is invalid")
        for name, keys in (("materialized_gitlinks", {"path", "commit", "materialized_sha256"}),
                           ("executable_files", {"path", "blob_sha256"})):
            values = row.get(name)
            if (not isinstance(values, list)
                    or [value.get("path") for value in values] != sorted(value.get("path") for value in values)
                    or any(not isinstance(value, dict) or set(value) != keys
                           or not isinstance(value.get("path"), str)
                           or (name == "materialized_gitlinks"
                               and (not commit.fullmatch(str(value.get("commit", "")))
                                    or not digest.fullmatch(str(value.get("materialized_sha256", "")))))
                           or (name == "executable_files"
                               and not digest.fullmatch(str(value.get("blob_sha256", ""))))
                           for value in values)):
                raise BenchmarkContractError("stage receipt repository nested row is invalid")
        symlinks = row.get("materialized_symlinks")
        if (not isinstance(symlinks, list)
                or [value.get("path") for value in symlinks] != sorted(value.get("path") for value in symlinks)
                or any(not _valid_stage_symlink_row(value, digest, commit) for value in symlinks)
                or len(symlinks) != counts["symlink"]
                or len(row["materialized_gitlinks"]) != counts["gitlink"]
                or len(row["executable_files"]) != counts["executable"]):
            raise BenchmarkContractError("stage receipt repository materialization lists are invalid")
    if expected is not None and (set(expected) - set(receipt)
                                 or any(receipt[key] != value for key, value in expected.items())):
        raise BenchmarkContractError("stage receipt authoritative relationships differ")
    if root is not None:
        identity: list[str] = []
        for directory in directories:
            path = root / directory
            if path.is_symlink() or not path.is_dir():
                raise BenchmarkContractError("stage receipt candidate directory differs")
            identity.append(f"D\0{directory}\n")
        for relative, value in files.items():
            path = root / relative
            if path.is_symlink() or not path.is_file() or sha256(path.read_bytes()).hexdigest() != value:
                raise BenchmarkContractError("stage receipt candidate file differs")
            identity.append(f"{'X' if path.stat().st_mode & 0o111 else 'F'}\0{relative}\0{value}\n")
        if sha256("".join(sorted(identity)).encode()).hexdigest() != receipt["candidate_tree_sha256"]:
            raise BenchmarkContractError("stage receipt candidate tree differs")
    return dict(receipt)


def _valid_stage_symlink_row(value: Any, digest: re.Pattern[str], commit: re.Pattern[str]) -> bool:
    if not isinstance(value, dict) or value.get("resolution") not in {
        "blob", "gitlink", "gitlink-path", "directory", "dangling",
    }:
        return False
    common = {"path", "link_blob_sha256", "materialized_sha256", "resolution", "final_path", "chain"}
    extras = {"blob": {"final_object", "final_mode"}, "directory": {"tree_sha256"}}.get(
        value["resolution"], set())
    chain = value.get("chain")
    return (set(value) == common | extras
            and all(isinstance(value.get(key), str) and value[key] for key in ("path", "final_path"))
            and digest.fullmatch(str(value.get("link_blob_sha256", ""))) is not None
            and digest.fullmatch(str(value.get("materialized_sha256", ""))) is not None
            and ("final_object" not in value or commit.fullmatch(str(value["final_object"])) is not None)
            and ("tree_sha256" not in value or digest.fullmatch(str(value["tree_sha256"])) is not None)
            and ("final_mode" not in value or value["final_mode"] in {"100644", "100755"})
            and isinstance(chain, list) and bool(chain)
            and all(isinstance(item, dict) and set(item) == {"path", "target", "resolved_path"}
                    and all(isinstance(item[key], str) and item[key] for key in item) for item in chain))


def _stable_regular_file_bytes(path: Path, label: str, *, read_only: bool = False) -> bytes:
    """Read one path-bound regular-file epoch without following a symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BenchmarkContractError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(named.st_mode)
                or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)):
            raise BenchmarkContractError(f"{label} must be a regular non-symlink file")
        if read_only and before.st_mode & 0o222:
            raise BenchmarkContractError(f"{label} must be read-only")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        if tuple(getattr(before, field) for field in fields) != tuple(getattr(after, field) for field in fields):
            raise BenchmarkContractError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _execute_opencode_in_root(
    spec: RunSpec,
    evaluator_root: Path,
    *,
    run=subprocess.run,
    environ: Mapping[str, str] | None = None,
    primary_task_enabled: bool = True,
    primary_phase: str | None = None,
    require_external_roles: bool = True,
    prompt_override: str | None = None,
    model_call_limit: int | None = None,
    public_bundle_binding: _ValidatedPublicBundleBinding | None = None,
    evidence_class: str | None = None,
) -> RunReceipt:
    """Execute one sealed run with real OpenCode preflight and native telemetry.

    ``debug agent`` and ``run`` use the same cwd and environment.  Missing
    provider usage/finish telemetry is a failed receipt, never fabricated.
    Process launch errors and timeouts are converted to auditable failures.
    """
    if evidence_class not in {None, "production", "test-only"}:
        raise BenchmarkContractError("invalid evidence class")
    injected_runner = (evidence_class == "test-only" if evidence_class is not None
                       else run is not subprocess.run)
    config = load_frozen_config()
    _regular_external_file(spec.isolated_policy, spec.public_bundle)
    policy = _load_json(spec.isolated_policy)
    expected = _track(config, spec.track, spec.candidate)
    if policy != expected:
        raise BenchmarkContractError("isolated policy does not exactly match the frozen track")
    if public_bundle_binding is None:
        command = build_opencode_command(
            spec.task, spec.public_bundle, spec.candidate, spec.track, config,
            validate_bundle=True,
        )
        runtime_bundle_binding = _capture_validated_public_bundle_binding(
            spec.public_bundle,
            "pangea-data" if spec.candidate == "pangea" else "codetalks-data",
        )
    else:
        _validate_bound_public_bundle(spec.public_bundle, public_bundle_binding)
        command = build_opencode_command(
            spec.task, spec.public_bundle, spec.candidate, spec.track, config,
            validate_bundle=False,
        )
        runtime_bundle_binding = public_bundle_binding
    _validate_runspec_case_binding(spec, runtime_bundle_binding)
    def revalidate_runtime_bundle() -> None:
        _validate_bound_public_bundle(spec.public_bundle, runtime_bundle_binding)
    if prompt_override is not None:
        if not isinstance(prompt_override, str) or not prompt_override.strip():
            raise BenchmarkContractError("primary prompt override must be non-empty")
        command[-1] = prompt_override
    agent = config["candidates"][spec.candidate]["agent"]
    if model_call_limit is None:
        model_call_limit = config["runtime"]["max_model_calls"]
    if type(model_call_limit) is not int or not 0 <= model_call_limit <= config["runtime"]["max_model_calls"]:
        raise BenchmarkContractError("model-call limit exceeds the frozen aggregate budget")
    base_policy_receipt = {
        "track": spec.track,
        "policy_mode": expected["policy_mode"],
        "policy_sha256": _canonical_hash(expected),
        "model_call_limit": model_call_limit,
        "public_case_path": spec.public_case_path,
        "public_case_sha256": spec.public_case_sha256,
    }
    if model_call_limit == 0:
        receipt = _empty_receipt(
            spec, command, ["budget_exceeded"],
            policy={**base_policy_receipt, "pre_request_budget_blocked": True},
        )
        receipt.telemetry.update({
            "model_call_limit": 0,
            "model_calls_completed": 0,
            "model_requests_admitted": 0,
            "pre_request_budget_blocked": True,
            "pre_request_budget_enforced": True,
            "injected_test_runner": injected_runner,
        })
        return receipt
    env, env_receipt, provider_available, budget_hook = _execution_environment(
        spec, expected, agent, environ, evaluator_root,
        primary_task_enabled=primary_task_enabled,
        primary_phase=primary_phase,
        model_call_limit=model_call_limit,
    )
    policy_receipt: dict[str, Any] = {
        **base_policy_receipt,
        **env_receipt,
    }
    common = {"cwd": spec.public_bundle, "capture_output": True, "text": True, "check": False, "env": env}
    if (common["cwd"] != spec.public_bundle or command.count("--dir") != 1
            or command[command.index("--dir") + 1] != str(spec.public_bundle)):
        raise BenchmarkContractError("primary subprocess is not anchored to the public bundle")
    policy_receipt["primary_subprocess_cwd"] = "public-bundle"
    base_env = dict(env)
    base_env.pop("OPENCODE_CONFIG_CONTENT", None)
    base_common = {**common, "env": base_env}
    if not provider_available:
        return _empty_receipt(spec, command, ["provider_unavailable"], environment=env_receipt, policy=policy_receipt)
    try:
        version = run(["opencode", "--version"], timeout=30, **common)
    except (OSError, subprocess.TimeoutExpired):
        return _empty_receipt(spec, command, ["version_preflight_launch_error"], environment=env_receipt, policy=policy_receipt)
    if version.returncode != 0 or _text(version.stdout).strip() != config["runtime"]["opencode_version"]:
        return _empty_receipt(spec, command, ["opencode_version_mismatch"], exit_code=version.returncode,
                              stdout=_text(version.stdout), stderr=_text(version.stderr), environment=env_receipt, policy=policy_receipt)
    revalidate_runtime_bundle()
    try:
        resolved_config = run(
            ["opencode", "debug", "config"],
            timeout=config["runtime"]["opencode_debug_timeout_seconds"], **common,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _empty_receipt(
            spec, command, ["plugin_closure_preflight_launch_error"],
            environment=env_receipt, policy=policy_receipt,
        )
    if resolved_config.returncode != 0:
        return _empty_receipt(
            spec, command, ["plugin_closure_preflight_nonzero"],
            exit_code=resolved_config.returncode, environment=env_receipt, policy=policy_receipt,
        )
    plugin_closure, plugin_failures = _resolved_plugin_closure(
        _text(resolved_config.stdout), budget_hook, evaluator_root / "opencode-env",
    )
    policy_receipt["resolved_plugin_closure"] = plugin_closure
    if plugin_failures:
        return _empty_receipt(
            spec, command, plugin_failures, environment=env_receipt, policy=policy_receipt,
        )
    workers = _required_workers(spec) if spec.track == "as-shipped" else []
    isolated_environment_root = evaluator_root / "opencode-env"
    if spec.track == "equal-tools":
        global_permission = _equal_tools_override(agent)["permission"]
    elif spec.candidate == "fuse":
        global_permission = _fuse_safety_overlay(agent)["permission"]
    else:
        global_permission = _as_shipped_safety_overlay(
            agent, workers, primary_task_enabled=primary_task_enabled, primary_phase=primary_phase,
        )["permission"]
    baseline: dict[str, Any] = {}
    if spec.track == "as-shipped":
        for target_agent in [agent, *workers]:
            revalidate_runtime_bundle()
            try:
                raw_debug = run(
                    ["opencode", "debug", "agent", target_agent],
                    timeout=config["runtime"]["opencode_debug_timeout_seconds"], **base_common,
                )
            except (OSError, subprocess.TimeoutExpired):
                return _empty_receipt(spec, command, ["worker_topology_preflight_failed"], environment=env_receipt, policy=policy_receipt)
            parsed_baseline = _baseline_agent_receipt(
                _text(raw_debug.stdout), target_agent, worker=target_agent != agent,
                isolated_root=isolated_environment_root, public_bundle=spec.public_bundle,
            ) if raw_debug.returncode == 0 else None
            if parsed_baseline is None:
                return _empty_receipt(spec, command, ["worker_topology_preflight_failed"], exit_code=raw_debug.returncode,
                                      environment=env_receipt, policy=policy_receipt)
            baseline[target_agent] = parsed_baseline
    resolved_agents: dict[str, Any] = {}
    preflight_failures: list[str] = []
    for target_agent in [agent, *workers]:
        revalidate_runtime_bundle()
        try:
            debug = run(
                ["opencode", "debug", "agent", target_agent],
                timeout=config["runtime"]["opencode_debug_timeout_seconds"], **common,
            )
        except (OSError, subprocess.TimeoutExpired):
            return _empty_receipt(spec, command, ["agent_preflight_launch_error"], environment=env_receipt, policy=policy_receipt)
        if debug.returncode != 0:
            return _empty_receipt(spec, command, ["agent_preflight_nonzero"], exit_code=debug.returncode,
                                  stdout=_text(debug.stdout), stderr=_text(debug.stderr), environment=env_receipt, policy=policy_receipt)
        resolved, agent_failures = _resolved_agent_receipt(
            _text(debug.stdout), target_agent, spec.track, expected, worker=target_agent != agent, candidate=spec.candidate,
            primary_task_enabled=primary_task_enabled, isolated_root=isolated_environment_root,
            primary_phase=primary_phase,
            public_bundle=spec.public_bundle, global_permission=global_permission,
        )
        if target_agent in baseline and resolved.get("prompt_sha256") != baseline[target_agent]["prompt_sha256"]:
            agent_failures.append("candidate_prompt_not_preserved")
        resolved_agents[target_agent] = resolved
        preflight_failures.extend(agent_failures)
    preflight = dict(resolved_agents[agent])
    preflight["plugin_closure"] = plugin_closure
    preflight["workers"] = {name: resolved_agents[name] for name in workers}
    preflight["baseline_agent_hashes"] = {name: item["resolved_config_sha256"] for name, item in baseline.items()}
    policy_receipt.update({key: value for key, value in preflight.items() if key in {"enabled_tools", "resolved_config_sha256", "permission_rules_sha256"}})
    policy_receipt["worker_overlay_hashes"] = {name: item["resolved_config_sha256"] for name, item in preflight["workers"].items()}
    if preflight_failures:
        return _empty_receipt(spec, command, preflight_failures, environment=env_receipt, preflight=preflight, policy=policy_receipt)

    before_integrity = _bundle_integrity_snapshot(spec.public_bundle, expected.get("managed_write_roots", []))
    revalidate_runtime_bundle()
    started = time.monotonic()
    timed_out = False
    try:
        completed = run(command, timeout=config["runtime"]["max_wall_clock_seconds"], **common)
    except subprocess.TimeoutExpired as exc:
        completed = type("Timeout", (), {"returncode": -1, "stdout": exc.stdout or "", "stderr": exc.stderr or ""})()
        timed_out = True
    except OSError:
        duration = time.monotonic() - started
        integrity, integrity_failures = _compare_bundle_integrity(
            before_integrity, _bundle_integrity_snapshot(spec.public_bundle, expected.get("managed_write_roots", [])),
        )
        policy_receipt["bundle_integrity"] = integrity
        return _empty_receipt(spec, command, ["process_launch_error", *integrity_failures], duration=duration,
                              environment=env_receipt, preflight=preflight, policy=policy_receipt)
    duration = time.monotonic() - started
    stdout = _text(completed.stdout)
    stderr = _text(completed.stderr)
    telemetry = parse_jsonl_telemetry(
        stdout.splitlines(True), public_bundle=spec.public_bundle, track=expected,
        primary_phase=primary_phase,
    )
    budget_observation = _model_budget_observation(
        budget_hook, telemetry, injected_runner=injected_runner,
    )
    telemetry.update(budget_observation)
    policy_receipt["pre_request_budget_blocked"] = budget_observation["pre_request_budget_blocked"]
    failures: list[str] = []
    if spec.candidate == "fuse":
        try:
            final_receipt = collect_final_output(
                spec.public_bundle, telemetry["final_text"], evaluator_root=evaluator_root,
                expected_manifest_sha256=spec.candidate_manifest_sha256,
                expected_materialization=spec.candidate_materialization,
            )
            # Formal files, when present, are the scorer input.  Native text
            # remains hash-bound auxiliary evidence only.
            telemetry["candidate_final_content"] = final_receipt["formal_content"]
            policy_receipt["final_output_receipt"] = {
                key: value for key, value in final_receipt.items() if key != "formal_content"
            }
        except CodeTalksStagingError:
            failures.append("candidate_output_collection_failed")
    if timed_out or duration > config["runtime"]["max_wall_clock_seconds"]:
        failures.append("timeout")
    if completed.returncode != 0:
        failures.append("nonzero_exit")
    if telemetry["parse_errors"] or telemetry["schema_errors"]:
        failures.append("invalid_jsonl")
    if telemetry["native_errors"]:
        failures.append("native_error_event")
    if not telemetry["token_usage_observed"] or not telemetry["finish_reason_observed"]:
        failures.append("missing_budget_telemetry")
    if not telemetry["final_text"].strip():
        failures.append("missing_final_text")
    enabled = set(preflight["enabled_tools"])
    if set(telemetry["tool_names"]) - enabled or telemetry["network_tool_calls"]:
        failures.append("tool_or_network_violation")
    if telemetry["tool_policy_violations"]:
        failures.append("tool_input_policy_violation")
    if primary_phase == "intake":
        allowed_actions = [action for action in telemetry["tool_actions"]
                           if action.get("decision") == "allow:evaluator-intake-v2"]
        if (telemetry["tool_calls"] != 1 or telemetry["tool_names"] != ["bash"]
                or len(allowed_actions) != 1):
            failures.append("intake_one_shot_violation")
    if spec.track == "as-shipped" and spec.candidate == "fuse":
        if not any(action.get("tool") == "task" and action.get("target") == "general"
                   and action.get("decision") == "allow:frozen-worker" for action in telemetry["tool_actions"]):
            failures.append("independent_judge_missing")
    if spec.track=="as-shipped" and spec.candidate == "pangea":
        if any(action.get("tool")=="task" and action.get("target") in AS_SHIPPED_TASKS for action in telemetry["tool_actions"]):
            failures.append("same_process_leaf_task_forbidden")
        # This entry point executes only the primary phase.  Until a complete
        # evaluator-owned multi-stage composition consumes signed leaf-role
        # outputs, an as-shipped primary-only receipt is never score-eligible.
        if require_external_roles:
            failures.append("external_role_execution_required")
    if (budget_observation["pre_request_budget_blocked"]
            or telemetry["model_calls"] > model_call_limit
            or telemetry["tool_calls"] > expected["max_tool_calls"]):
        failures.append("budget_exceeded")
    if (not injected_runner and (budget_observation["pre_request_budget_enforced"] is not True
            or telemetry["model_calls"] > budget_observation["model_requests_admitted"])):
        failures.append("model_budget_hook_unverified")
    if telemetry["max_step_input_tokens"] > config["runtime"]["context_window"] or telemetry["max_step_output_tokens"] > config["runtime"]["max_output_tokens"]:
        failures.append("budget_exceeded")
    if telemetry["truncated"]:
        failures.append("truncated")
    integrity, integrity_failures = _compare_bundle_integrity(
        before_integrity, _bundle_integrity_snapshot(spec.public_bundle, expected.get("managed_write_roots", [])),
    )
    policy_receipt["bundle_integrity"] = integrity
    failures.extend(integrity_failures)
    return RunReceipt(
        candidate=spec.candidate,
        track=spec.track,
        case_id=spec.case_id,
        command=command,
        exit_code=completed.returncode,
        duration_seconds=duration,
        stdout_sha256=sha256(stdout.encode()).hexdigest(),
        stderr_sha256=sha256(stderr.encode()).hexdigest(),
        telemetry=_primary_receipt_telemetry(telemetry),
        environment_keys=env_receipt["environment_keys"],
        preflight=preflight,
        policy_receipt=policy_receipt,
        passed=not failures,
        failures=list(dict.fromkeys(failures)),
    )


def run_receipt_payload(receipt: RunReceipt) -> dict[str, Any]:
    """Convert the typed runner receipt without weakening its field bindings."""
    if not isinstance(receipt, RunReceipt):
        raise BenchmarkContractError("RunReceipt adapter requires the typed evaluator receipt")
    return asdict(receipt)


def execute_pangea_primary_phase(
    spec: RunSpec,
    phase: str,
    prompt: str,
    evaluator_root: Path,
    *,
    run=subprocess.run,
    environ: Mapping[str, str] | None = None,
    model_call_limit: int | None = None,
    public_bundle_binding: _ValidatedPublicBundleBinding | None = None,
    evidence_class: str | None = None,
) -> RunReceipt:
    """Execute one primary phase with leaf Task denied before process launch."""
    if spec.candidate != "pangea" or spec.track != "as-shipped":
        raise BenchmarkContractError("primary phases require the PANGEA as-shipped track")
    if phase not in {"intake", "resume", "finalize"} or not isinstance(prompt, str) or not prompt.strip():
        raise BenchmarkContractError("invalid primary phase request")
    evaluator_root = Path(evaluator_root).resolve()
    evaluator_root.mkdir(parents=True, exist_ok=True)
    receipt = _execute_opencode_in_root(
        spec, evaluator_root / phase, run=run, environ=environ,
        primary_task_enabled=False, require_external_roles=phase == "intake",
        primary_phase=phase,
        prompt_override=prompt, model_call_limit=model_call_limit,
        public_bundle_binding=public_bundle_binding,
        evidence_class=evidence_class,
    )
    receipt.policy_receipt["primary_phase"] = phase
    receipt.policy_receipt["phase_prompt_sha256"] = sha256(prompt.encode()).hexdigest()
    return receipt


def _finite_nonnegative(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and value >= 0


def _scoped_target(value: Any, public_bundle: Path) -> str | None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return None
    raw = value.strip()
    prefix = re.split(r"[*?\[]", raw, maxsplit=1)[0] or "."
    path = Path(prefix)
    if ".." in path.parts:
        return None
    resolved = (path if path.is_absolute() else public_bundle / path).resolve(strict=False)
    try:
        relative = resolved.relative_to(public_bundle.resolve())
    except ValueError:
        return None
    return relative.as_posix() or "."


def _audit_bash(tool_input: dict[str, Any], public_bundle: Path) -> tuple[str, str | None, str]:
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return "bash", None, "deny:missing-command"
    workdir = tool_input.get("workdir", tool_input.get("cwd", "."))
    target = _scoped_target(workdir, public_bundle)
    if target is None:
        return "bash", None, "deny:workdir-outside-bundle"
    if re.search(r"(?:&&|\|\||[;<>|`$~]|\r|\n)", command):
        return "bash", target, "deny:shell-composition-or-redirection"
    try:
        argv = shlex.split(command)
    except ValueError:
        return "bash", target, "deny:unparseable-command"
    if not argv:
        return "bash", target, "deny:missing-command"
    executable = Path(argv[0]).name
    lowered = [item.casefold() for item in argv]
    if any("=/" in item or "=.." in item for item in argv[1:]):
        return executable, target, "deny:option-path-outside-bundle"
    if executable.casefold() in {"curl", "wget", "nc", "netcat", "ssh", "scp", "rm", "mv", "cp"}:
        return executable, target, "deny:dangerous-executable"
    if any(item.startswith("/") or ".." in Path(item).parts for item in argv[1:] if not item.startswith("--repository-commit")):
        return executable, target, "deny:path-outside-bundle"
    if "codetalks-source-driven-blackbox-v2/scripts/run_guard.py" in command:
        if not (executable.startswith("python") and len(argv) >= 3
                and argv[1] == ".opencode/skills/codetalks-source-driven-blackbox-v2/scripts/run_guard.py"):
            return "codetalks-run-guard", target, "deny:invalid-codetalks-run-guard"
        subcommand = argv[2]
        if subcommand not in {"init", "ack-core", "start-step", "complete-step", "validate", "handoff", "finalize"}:
            return "codetalks-run-guard", target, "deny:unknown-codetalks-run-guard-command"
        arguments = argv[3:]
        if len(arguments) % 2:
            return "codetalks-run-guard", target, "deny:invalid-codetalks-run-guard-options"
        pairs = dict(zip(arguments[::2], arguments[1::2]))
        if len(pairs) * 2 != len(arguments):
            return "codetalks-run-guard", target, "deny:duplicate-codetalks-run-guard-option"
        schema = {
            "init": {"--skill-root", "--workspace", "--source-raw", "--source-verified", "--output", "--scenario", "--mode"},
            "ack-core": {"--workspace", "--rule", "--file"},
            "start-step": {"--workspace", "--step"}, "complete-step": {"--workspace", "--step"},
            "validate": {"--workspace"}, "handoff": {"--workspace"}, "finalize": {"--workspace"},
        }[subcommand]
        if set(pairs) != schema:
            return "codetalks-run-guard", target, "deny:unknown-codetalks-run-guard-option"
        if pairs.get("--workspace") != "codetalks-data":
            return "codetalks-run-guard", target, "deny:codetalks-run-guard-scope"
        if subcommand == "init" and (
                pairs["--skill-root"] != ".opencode/skills/codetalks-source-driven-blackbox-v2"
                or not pairs["--source-raw"].startswith("repositories/")
                or not pairs["--source-verified"].startswith("repositories/")
                or not (pairs["--output"] == "codetalks-data" or pairs["--output"].startswith("codetalks-data/"))
                or pairs["--mode"] != "depth"):
            return "codetalks-run-guard", target, "deny:codetalks-run-guard-scope"
        if subcommand == "ack-core" and not pairs["--file"].startswith(".opencode/skills/codetalks-source-driven-blackbox-v2/"):
            return "codetalks-run-guard", target, "deny:codetalks-run-guard-scope"
        if subcommand in {"start-step", "complete-step"} and pairs["--step"] not in {f"{number:02d}" for number in range(1, 10)}:
            return "codetalks-run-guard", target, "deny:codetalks-run-guard-scope"
        return "codetalks-run-guard", target, "allow:codetalks-run-guard"
    if executable == "git":
        if len(argv) < 2 or argv[1] not in {"status", "diff", "log", "show", "rev-parse", "ls-files", "grep"}:
            return "git", target, "deny:git-subcommand"
        unsafe_git = {"-C", "--output", "--ext-diff", "--textconv", "--open-files-in-pager"}
        if any(item in unsafe_git or item.startswith("--output=") for item in argv[2:]):
            return f"git-{argv[1]}", target, "deny:git-write-or-scope-option"
        return f"git-{argv[1]}", target, "allow:readonly-git"
    if executable == "rg":
        if any(item == "--pre" or item.startswith("--pre=") for item in argv[1:]):
            return "rg", target, "deny:rg-preprocessor"
        return "rg", target, "allow:readonly-command"
    if executable in {"ls", "head", "tail", "wc"}:
        return executable, target, "allow:readonly-command"
    if executable == "sed":
        if any(item == "-i" or item.startswith("-i") for item in argv[1:]):
            return "sed", target, "deny:sed-in-place"
        scripts = [item for item in argv[1:] if not item.startswith("-")]
        if not scripts or not re.fullmatch(r"\d+(?:,\d+)?p", scripts[0]):
            return "sed", target, "deny:sed-script-not-print-only"
        return "sed", target, "allow:readonly-command"
    if executable == "find":
        if any(item in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for item in lowered[1:]):
            return "find", target, "deny:find-mutation-or-exec"
        return "find", target, "allow:readonly-command"
    if executable.startswith("python"):
        if len(argv) >= 2 and argv[1] == "runtime/runctl.py":
            return "python-runctl", target, "allow:managed-runtime"
        if len(argv) >= 3 and argv[1:3] == ["-m", "tooling.pangea_cli"]:
            return "python-pangea-cli", target, "allow:managed-runtime"
        return "python", target, "deny:arbitrary-python"
    return "unrecognized-command", target, "deny:command-not-allowlisted"


def _audit_tool_input(tool: str, tool_input: Any, public_bundle: Path | None,
                      track: dict[str, Any] | None, primary_phase: str | None = None) -> dict[str, Any]:
    action: dict[str, Any] = {"tool": tool, "action": tool, "target": None, "decision": "unreviewed"}
    if public_bundle is None or track is None:
        return action
    if primary_phase == "intake":
        if tool != "bash":
            action["decision"] = "deny:intake-tool-not-allowlisted"
            return action
        if (not isinstance(tool_input, dict)
                or tool_input.get("command") != EVALUATOR_INTAKE_COMMAND):
            action.update(action="python-runctl", decision="deny:intake-runtime-input")
            return action
        if set(tool_input) == {"command"}:
            target = "implicit-public-bundle"
        elif set(tool_input) == {"command", "workdir"} and tool_input.get("workdir") == ".":
            target = "explicit-dot"
        else:
            action.update(action="python-runctl", decision="deny:intake-runtime-input")
            return action
        action.update(action="python-runctl", target=target, decision="allow:evaluator-intake-v2")
        return action
    if not isinstance(tool_input, dict):
        action["decision"] = "deny:missing-or-unknown-input"
        return action
    if tool in {"read", "glob", "grep"}:
        value = next((tool_input[name] for name in ("filePath", "path", "directory") if name in tool_input), None)
        target = _scoped_target(value, public_bundle)
        action.update(target=target, decision="allow:bundle-path" if target is not None else "deny:path-outside-bundle-or-missing")
    elif tool in {"write", "edit"}:
        value = next((tool_input[name] for name in ("filePath", "path") if name in tool_input), None)
        target = _scoped_target(value, public_bundle)
        allowed = target is not None and (target == "codetalks-data" or target.startswith("codetalks-data/"))
        action.update(target=target, decision="allow:codetalks-managed-output" if allowed else "deny:write-outside-codetalks-data")
    elif tool == "task":
        target = next((tool_input[name] for name in ("subagent_type", "agent", "name") if isinstance(tool_input.get(name), str)), None)
        allowed = target in track.get("task_allowlist", [])
        action.update(target=target if allowed else "unrecognized", action="dispatch", decision="allow:frozen-worker" if allowed else "deny:worker-not-allowlisted")
    elif tool == "skill":
        target = next((tool_input[name] for name in ("name", "skill") if isinstance(tool_input.get(name), str)), None)
        allowed = target in track.get("skill_allowlist", [])
        action.update(target=target if allowed else "unrecognized", action="load-skill", decision="allow:frozen-skill" if allowed else "deny:skill-not-frozen")
    elif tool == "bash":
        name, target, decision = _audit_bash(tool_input, public_bundle)
        action.update(action=name, target=target, decision=decision)
    else:
        action["decision"] = "deny:tool-not-allowlisted"
    return action


def _tool_input_policy_summary(actions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for action in actions:
        decision = action.get("decision")
        if isinstance(decision, str) and decision.startswith("deny:"):
            category = TOOL_INPUT_POLICY_CATEGORY_BY_DECISION.get(decision, "tool_not_allowlisted")
            counts[category] += 1
    return {
        "schema_version": "1.0",
        "total": sum(counts.values()),
        "category_counts": dict(sorted(counts.items())),
    }


def _primary_receipt_telemetry(telemetry: dict[str, Any]) -> dict[str, Any]:
    """Remove rejected-input detail after policy evaluation, retaining counts."""
    sanitized = dict(telemetry)
    sanitized["tool_actions"] = [
        dict(action) for action in telemetry.get("tool_actions", [])
        if not str(action.get("decision", "")).startswith("deny:")
    ]
    sanitized["tool_policy_violations"] = [
        {"category": category, "count": count}
        for category, count in telemetry["tool_input_policy_violation_summary"]["category_counts"].items()
    ]
    return sanitized


def parse_jsonl_telemetry(
    lines: Iterable[str], *, public_bundle: Path | None = None, track: dict[str, Any] | None = None,
    primary_phase: str | None = None,
) -> dict[str, Any]:
    """Parse the native OpenCode 1.18.4 ``run --format json`` event stream."""
    events: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    schema_errors: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    text_parts: list[str] = []
    finish_reasons: list[str] = []
    tool_names: list[str] = []
    input_total = output_total = reasoning_total = cache_read_total = cache_write_total = 0
    max_input = max_output = 0
    native_errors: list[dict[str, Any]] = []
    tool_actions: list[dict[str, Any]] = []
    session_ids: set[str] = set()
    step_count = tool_count = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            parse_errors.append({"line": line_number, "error": str(exc)})
            continue
        if not isinstance(event, dict):
            parse_errors.append({"line": line_number, "error": "event must be object"})
            continue
        event_type = event.get("type")
        if not isinstance(event_type, str) or event_type not in NATIVE_EVENT_TYPES:
            schema_errors.append({"line": line_number, "error": f"unknown native event type: {event_type!r}"})
            events.append({
                "type": str(event_type),
                "event_sha256": _canonical_hash(event),
            })
            continue
        if not _finite_nonnegative(event.get("timestamp")) or not isinstance(event.get("sessionID"), str) or not event.get("sessionID"):
            schema_errors.append({"line": line_number, "error": "event requires timestamp and sessionID"})
        else:
            session_ids.add(event["sessionID"])
        counts[event_type] += 1
        events.append({
            "type": event_type,
            "timestamp": event.get("timestamp"),
            "session_id_sha256": sha256(str(event.get("sessionID", "")).encode()).hexdigest(),
        })
        if event_type == "error":
            if "error" not in event:
                schema_errors.append({"line": line_number, "error": "error event requires error"})
            native_errors.append(event.get("error") if isinstance(event.get("error"), dict) else {"error": str(event.get("error"))})
            continue
        part = event.get("part")
        expected_part_type = {
            "step_start": "step-start",
            "step_finish": "step-finish",
            "tool_use": "tool",
        }.get(event_type, event_type)
        if not isinstance(part, dict) or part.get("type") != expected_part_type:
            schema_errors.append({"line": line_number, "error": f"{event_type} requires native {expected_part_type} part"})
            continue
        if event_type == "text":
            if not isinstance(part.get("text"), str):
                schema_errors.append({"line": line_number, "error": "text part requires text"})
            elif part["text"].strip():
                text_parts.append(part["text"])
        elif event_type == "tool_use":
            tool = part.get("tool")
            state = part.get("state")
            if not isinstance(tool, str) or not isinstance(state, dict) or state.get("status") not in {"completed", "error"}:
                schema_errors.append({"line": line_number, "error": "tool_use requires completed/error tool part"})
            else:
                tool_count += 1
                tool_names.append(tool)
                tool_input = state.get("input")
                if tool_input is None:
                    tool_input = part.get("input")
                tool_actions.append(_audit_tool_input(tool, tool_input, public_bundle, track, primary_phase))
        elif event_type == "step_finish":
            reason = part.get("reason")
            tokens = part.get("tokens")
            cache = tokens.get("cache") if isinstance(tokens, dict) else None
            values = [tokens.get(name) if isinstance(tokens, dict) else None for name in ("input", "output", "reasoning")]
            cache_values = [cache.get(name) if isinstance(cache, dict) else None for name in ("read", "write")]
            if not isinstance(reason, str) or not reason or not all(_finite_nonnegative(value) for value in values + cache_values):
                schema_errors.append({"line": line_number, "error": "step_finish requires reason and complete nonnegative token usage"})
            else:
                step_count += 1
                finish_reasons.append(reason)
                input_tokens, output_tokens, reasoning_tokens = map(int, values)
                cache_read, cache_write = map(int, cache_values)
                input_total += input_tokens
                output_total += output_tokens
                reasoning_total += reasoning_tokens
                cache_read_total += cache_read
                cache_write_total += cache_write
                max_input = max(max_input, input_tokens)
                max_output = max(max_output, output_tokens)
    network_calls = [name for name in tool_names if name.lower() in NETWORK_TOOL_NAMES]
    truncated = any(reason.lower() in {"length", "max_tokens", "content-filter", "content_filter"} for reason in finish_reasons)
    violation_actions = [
        item for item in tool_actions
        if not item["decision"].startswith("allow:") and item["decision"] != "unreviewed"
    ]
    return {
        "events": events,
        "event_counts": dict(sorted(counts.items())),
        "parse_errors": parse_errors,
        "schema_errors": schema_errors,
        "complete": not parse_errors and not schema_errors,
        "model_calls": step_count,
        "tool_calls": tool_count,
        "tool_names": tool_names,
        "tool_actions": tool_actions,
        "tool_policy_violations": violation_actions,
        "tool_input_policy_violation_summary": _tool_input_policy_summary(violation_actions),
        "network_tool_calls": network_calls,
        "input_tokens": input_total,
        "output_tokens": output_total,
        "reasoning_tokens": reasoning_total,
        "cache_read_tokens": cache_read_total,
        "cache_write_tokens": cache_write_total,
        "max_step_input_tokens": max_input,
        "max_step_output_tokens": max_output,
        "text_parts": text_parts,
        "final_text": "\n".join(text_parts),
        "finish_reasons": finish_reasons,
        "final_finish_reason": finish_reasons[-1] if finish_reasons else None,
        "token_usage_observed": step_count > 0,
        "finish_reason_observed": bool(finish_reasons),
        "truncated": truncated,
        "native_errors": native_errors,
        "session_ids": sorted(session_ids),
    }

_REPORT_AUDIT_ARTIFACTS=frozenset({"TASK_CONTRACT.json","ANALYSIS_MODEL.json","COVERAGE_JUDGE.json",
                                   "RISK_LEDGER.json","REPORT_MODEL.json"})
_ROLE_ARTIFACT_NAMES={"analysis-worker":frozenset({"CONTEXT.json"}),
                      "auditor":frozenset({"CLAIM.json","FACTS.json"}),
                      "mr-reader":frozenset({"MR_CONTEXT.json"})}
_ROLE_PROMPTS={"analysis-worker":"Analyze only CONTEXT.json and emit the required fragment JSON.",
               "auditor":"Assess CLAIM.json only against FACTS.json and emit supported/reason JSON.",
               "mr-reader":"Read only MR_CONTEXT.json and emit the requested regression summary."}


def _frozen_leaf_agent_definition(agent: str, overlay: dict[str, Any], *, primary_alias: bool = False) -> dict[str, Any]:
    """Bind an artifact-only role to its frozen repository prompt, not cwd discovery."""
    source = ROOT / ".opencode" / "agents" / f"{agent}.md"
    raw = source.read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    if len(parts) != 3:
        raise BenchmarkContractError("frozen leaf agent frontmatter is malformed")
    header, prompt = parts[1], parts[2].strip()
    description = re.search(r"(?m)^description:\s*(.+)$", header)
    mode = re.search(r"(?m)^mode:\s*(\S+)\s*$", header)
    hidden = re.search(r"(?m)^hidden:\s*(true|false)\s*$", header)
    temperature = re.search(r"(?m)^temperature:\s*([0-9.]+)\s*$", header)
    if (not description or not mode or mode.group(1) != "subagent" or not hidden
            or hidden.group(1) != "true" or not temperature or not prompt):
        raise BenchmarkContractError("frozen leaf agent identity is invalid")
    return {
        "description": description.group(1).strip(),
        "mode": "primary" if primary_alias else "subagent",
        "hidden": False if primary_alias else True,
        "temperature": float(temperature.group(1)),
        "prompt": prompt,
        **overlay,
    }

def _role_prompt(agent:str,artifacts:Mapping[str,Any]) -> str:
    if agent=="analysis-worker" and set(artifacts)=={"COMPACT_CONTEXT.json"}:
        return ("Analyze the inline compact context without tools. Emit one exact compact-analysis-v1 JSON "
                "object using only ordinal references and the frozen byte bounds.")
    if agent=="auditor" and set(artifacts)=={"SEMANTIC_BATCH.json"}:
        return ("Audit every inline ordinal claim against only its referenced facts without tools. Emit one exact "
                "compact batch JSON object with one supported/reason row per ordinal.")
    if agent=="auditor" and set(artifacts)==set(_REPORT_AUDIT_ARTIFACTS):
        return ("Audit REPORT_MODEL.json only against the four fixed bound artifacts. Emit one exact "
                "audit-opinion schema v2 JSON object; do not use any other input.")
    return _ROLE_PROMPTS[agent]

def _role_environment(agent:str,environment_root:Path,source:Mapping[str,str]|None,
                      public_bundle:Path,model_call_limit:int=40,tool_free:bool=False) -> tuple[dict[str,str],dict[str,Any],bool,dict[str,Any],str]:
    inherited=os.environ if source is None else source
    env={key:value for key,value in inherited.items() if key in ENVIRONMENT_ALLOWLIST}
    for name in ("home","config","data","cache","tool-output"): (environment_root/name).mkdir(parents=True,exist_ok=True,mode=0o700)
    env.update({"HOME":str(environment_root/"home"),"XDG_CONFIG_HOME":str(environment_root/"config"),
                "XDG_DATA_HOME":str(environment_root/"data"),"XDG_CACHE_HOME":str(environment_root/"cache"),
                "TMPDIR":str(environment_root/"tool-output"),"TMP":str(environment_root/"tool-output"),
                "TEMP":str(environment_root/"tool-output")})
    env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
    env["DEEPSEEK_BASE_URL"] = DEEPSEEK_OFFICIAL_BASE_URL
    overlay=_as_shipped_safety_overlay("pangea-test",[agent])["agent"][agent]
    if tool_free:
        overlay["tools"]={name:False for name in overlay["tools"]};overlay["permission"]={"*":"deny"}
    execution_agent=COMPACT_EXECUTION_AGENTS[agent] if tool_free else agent
    config_overlay=_frozen_deepseek_provider_overlay()
    config_overlay["agent"]={execution_agent:_frozen_leaf_agent_definition(agent,overlay,primary_alias=tool_free)}
    hook=_install_model_budget_hook(config_overlay,environment_root,model_call_limit)
    _verified_model_budget_hook_uri(hook,environment_root)
    env["OPENCODE_DISABLE_DEFAULT_PLUGINS"]="1"
    env["OPENCODE_CONFIG_CONTENT"]=json.dumps(config_overlay,sort_keys=True,separators=(",",":"))
    env["OPENCODE_EVALUATOR_CANDIDATE_NETWORK"]="disabled"; env["OPENCODE_EVALUATOR_PROVIDER_TRANSPORT"]="required"
    provider_available=_project_deepseek_credentials(inherited,env,environment_root/"data",public_bundle)
    return env,overlay,provider_available,hook,execution_agent

def _minimal_cwd_manifest(cwd:Path) -> tuple[list[dict[str,str]],str]:
    rows=[]
    for path in sorted(cwd.iterdir(),key=lambda value:value.name):
        if path.is_symlink() or not path.is_file(): raise BenchmarkContractError("role cwd contains non-regular entry")
        rows.append({"name":path.name,"sha256":sha256(path.read_bytes()).hexdigest()})
    return rows,_canonical_hash(rows)

def _execute_isolated_role_in_root(agent:str,artifacts:Mapping[str,Any],root:Path,environment_root:Path,
                                   *,run=subprocess.run,environ:Mapping[str,str]|None=None,
                                   model_call_limit:int|None=None,evidence_class:str|None=None) -> TrustedRoleExecution:
    if evidence_class not in {None, "production", "test-only"}: raise BenchmarkContractError("invalid evidence class")
    injected_runner = evidence_class == "test-only" if evidence_class is not None else run is not subprocess.run
    valid_sets = ({_ROLE_ARTIFACT_NAMES[agent], _REPORT_AUDIT_ARTIFACTS}
                  if agent == "auditor" else {_ROLE_ARTIFACT_NAMES.get(agent, frozenset())})
    if agent == "auditor": valid_sets.add(frozenset({"SEMANTIC_BATCH.json"}))
    if agent == "analysis-worker": valid_sets.add(frozenset({"COMPACT_CONTEXT.json"}))
    if agent not in _ROLE_ARTIFACT_NAMES or frozenset(artifacts) not in valid_sets:
        raise BenchmarkContractError("role artifact closure mismatch")
    compact_artifact=frozenset(artifacts) in {frozenset({"COMPACT_CONTEXT.json"}),frozenset({"SEMANTIC_BATCH.json"})}
    if compact_artifact and any(len(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode())
                                > ROLE_INPUT_SAFETY_LIMIT for value in artifacts.values()):
        raise BenchmarkContractError("compact role input exceeds frozen byte limit")
    cwd=root/"cwd"; cwd.mkdir()
    bindings=[]
    for name in sorted(artifacts):
        value=artifacts[name]; content=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n").encode()
        path=cwd/name; path.write_bytes(content); path.chmod(0o400)
        bindings.append({"name":name,"payload_sha256":_canonical_hash(value),"file_sha256":sha256(content).hexdigest()})
    manifest_rows,manifest_hash=_minimal_cwd_manifest(cwd)
    if [row["name"] for row in manifest_rows]!=sorted(artifacts): raise BenchmarkContractError("role cwd is not minimal")
    if _opencode_project_plugin_entries(cwd):
        raise BenchmarkContractError("role cwd contains OpenCode project plugin entries")
    runtime=load_frozen_config()["runtime"]
    if model_call_limit is None: model_call_limit=runtime["max_model_calls"]
    if type(model_call_limit) is not int or not 0<=model_call_limit<=runtime["max_model_calls"]:
        raise BenchmarkContractError("model-call limit exceeds the frozen aggregate budget")
    tool_free=compact_artifact
    execution_agent=COMPACT_EXECUTION_AGENTS[agent] if tool_free else agent
    command=["opencode","run","--dir",str(cwd),"--agent",execution_agent,"--model",runtime["model"],
             "--format","json","--print-logs",_role_prompt(agent,artifacts)]
    base={"artifact_type":"role_execution_receipt","schema_version":"1.0","captured_by":"evaluator",
          "agent":agent,"logical_role":agent,"execution_agent":execution_agent,
          "model":runtime["model"],"opencode_version":runtime["opencode_version"],
          "cwd_manifest_sha256":manifest_hash,"artifact_bindings":bindings,
          "command_sha256":_canonical_hash(command),"model_call_limit":model_call_limit,
          "evidence_class":"test-only" if injected_runner else "production"}
    if model_call_limit==0:
        receipt={**base,"overlay_sha256":None,"model_budget_hook_sha256":None,
                 "model_calls_completed":0,"model_requests_admitted":0,
                 "pre_request_budget_blocked":True,"pre_request_budget_enforced":True,
                 "injected_test_runner":injected_runner,
                 "resolved_config_sha256":None,"resolved_permission_rules_sha256":None,
                 "output_payload_sha256":None,"session_id":None,
                 "stdout_sha256":sha256(b"").hexdigest(),"exit_code":-1,
                 "passed":False,"failures":["budget_exceeded"]}
        return TrustedRoleExecution(receipt,"",_EXECUTION_AUTHORITY)
    env,overlay,provider_available,budget_hook,resolved_execution_agent=_role_environment(
        agent,environment_root,environ,cwd,model_call_limit,tool_free=tool_free,
    )
    if resolved_execution_agent != execution_agent:
        raise BenchmarkContractError("compact execution alias drift")
    base.update({"overlay_sha256":_canonical_hash(overlay),
                 "model_budget_hook_sha256":budget_hook["plugin_sha256"],
                 "plugin_closure":_plugin_closure_receipt(budget_hook)})
    common={"cwd":cwd,"capture_output":True,"text":True,"check":False,"env":env}
    failures=[]; stdout=""; exit_code=-1; session_id=None; output_payload_sha256=None; resolved_receipt:dict[str,Any]={}
    if not provider_available:
        failures.append("provider_unavailable")
    else:
        try: version=run(["opencode","--version"],timeout=30,**common)
        except (OSError,subprocess.TimeoutExpired): version=None; failures.append("version_preflight_failed")
        if version is not None and (version.returncode!=0 or _text(version.stdout).strip()!=runtime["opencode_version"]): failures.append("opencode_version_mismatch")
        if not failures:
            try: config_debug=run(
                ["opencode","debug","config"],
                timeout=runtime["opencode_debug_timeout_seconds"],**common,
            )
            except (OSError,subprocess.TimeoutExpired): config_debug=None; failures.append("plugin_closure_preflight_failed")
            if config_debug is not None:
                closure_receipt,closure_failures=_resolved_plugin_closure(
                    _text(config_debug.stdout),budget_hook,environment_root,
                )
                base["resolved_plugin_closure"]=closure_receipt
                if config_debug.returncode!=0 or closure_failures: failures.append("plugin_closure_preflight_failed")
        if not failures:
            try: debug=run(
                ["opencode","debug","agent",execution_agent],
                timeout=runtime["opencode_debug_timeout_seconds"],**common,
            )
            except (OSError,subprocess.TimeoutExpired): debug=None; failures.append("agent_preflight_failed")
            if debug is not None:
                resolved_receipt,resolved_failures=_resolved_agent_receipt(
                    _text(debug.stdout),execution_agent,"as-shipped",_track(load_frozen_config(),"as-shipped"),
                    worker=not tool_free,isolated_root=environment_root,tool_free=tool_free,
                )
                if debug.returncode!=0 or resolved_failures: failures.append("agent_preflight_failed")
        if not failures:
            try: completed=run(command,timeout=runtime["max_wall_clock_seconds"],**common)
            except (OSError,subprocess.TimeoutExpired): completed=None; failures.append("provider_execution_failed")
            if completed is not None:
                exit_code=completed.returncode; stdout=_text(completed.stdout); telemetry=parse_jsonl_telemetry(stdout.splitlines(True),public_bundle=cwd,track=_track(load_frozen_config(),"as-shipped"))
                budget_observation=_model_budget_observation(
                    budget_hook,telemetry,injected_runner=injected_runner,
                )
                if (exit_code!=0 or telemetry["parse_errors"] or telemetry["schema_errors"] or telemetry["native_errors"]
                        or telemetry["model_calls"]<1
                        or telemetry["final_finish_reason"]!="stop" or telemetry["truncated"] or not telemetry["final_text"].strip()
                        or budget_observation["pre_request_budget_blocked"]
                        or telemetry["model_calls"]>model_call_limit
                        or telemetry["max_step_input_tokens"]>ROLE_INPUT_SAFETY_LIMIT
                        or telemetry["max_step_output_tokens"]>FROZEN_OUTPUT_LIMIT):
                    failures.append("provider_execution_failed")
                if budget_observation["pre_request_budget_blocked"]: failures.append("budget_exceeded")
                if (not injected_runner and (budget_observation["pre_request_budget_enforced"] is not True
                        or telemetry["model_calls"]>budget_observation["model_requests_admitted"])):
                    failures.append("model_budget_hook_unverified")
                if len(telemetry["session_ids"])!=1: failures.append("session_binding_failed")
                else: session_id=telemetry["session_ids"][0]
                try: output_payload_sha256=_canonical_hash(json.loads(telemetry["final_text"]))
                except (json.JSONDecodeError,TypeError): failures.append("provider_execution_failed")
                allowed=frozenset() if tool_free else AS_SHIPPED_ROLE_TOOLS[agent]
                if set(telemetry["tool_names"])-set(allowed) or telemetry["tool_policy_violations"]: failures.append("role_tool_policy_violation")
                if tool_free and len(telemetry["final_text"].encode())>FROZEN_OUTPUT_LIMIT: failures.append("native_output_byte_limit_exceeded")
    try: _,after_hash=_minimal_cwd_manifest(cwd)
    except BenchmarkContractError: after_hash=""; failures.append("cwd_changed")
    if after_hash!=manifest_hash: failures.append("cwd_changed")
    budget_observation=locals().get("budget_observation",{
        "model_calls_completed":0,"model_requests_admitted":0,
        "pre_request_budget_blocked":False,"pre_request_budget_enforced":False,
        "injected_test_runner":injected_runner,
    })
    receipt={**base,**budget_observation,
             "resolved_config_sha256":resolved_receipt.get("resolved_config_sha256"),
             "resolved_permission_rules_sha256":resolved_receipt.get("permission_rules_sha256"),
             "output_payload_sha256":output_payload_sha256,
             "session_id":session_id,"stdout_sha256":sha256(stdout.encode()).hexdigest(),
             "exit_code":exit_code,"passed":not failures,"failures":list(dict.fromkeys(failures))}
    return TrustedRoleExecution(receipt,stdout,_EXECUTION_AUTHORITY)


def execute_isolated_role(agent:str,artifacts:Mapping[str,Any],*,run=subprocess.run,
                          environ:Mapping[str,str]|None=None,scratch_parent:Path|None=None,
                          model_call_limit:int|None=None,evidence_class:str|None=None) -> TrustedRoleExecution:
    """Run one leaf role in its own process, environment, and artifact-only cwd."""
    parent=str(scratch_parent.resolve()) if scratch_parent is not None else None
    root=Path(tempfile.mkdtemp(prefix="pangea-role-",dir=parent))
    try:
        with tempfile.TemporaryDirectory(prefix="pangea-role-env-") as environment:
            return _execute_isolated_role_in_root(
                agent,artifacts,root,Path(environment),run=run,environ=environ,
                model_call_limit=model_call_limit,evidence_class=evidence_class,
            )
    finally:
        shutil.rmtree(root,ignore_errors=True)

def _validated_worker_execution(run_dir:Path,context_path:Path,execution:TrustedRoleExecution) -> tuple[dict[str,Any],dict[str,Any],dict[str,Any],dict[str,Any]]:
    if not isinstance(execution,TrustedRoleExecution): raise BenchmarkContractError("trusted analysis-worker execution receipt required")
    execution_receipt,jsonl=execution._trusted_payload()
    if (execution_receipt.get("captured_by")!="evaluator" or execution_receipt.get("agent")!="analysis-worker"
            or execution_receipt.get("opencode_version")!="1.18.4" or execution_receipt.get("passed") is not True
            or execution_receipt.get("failures")!=[] or execution_receipt.get("stdout_sha256")!=sha256(jsonl.encode()).hexdigest()
            or not re.fullmatch(r"[a-f0-9]{64}",str(execution_receipt.get("resolved_config_sha256","")))
            or not re.fullmatch(r"[a-f0-9]{64}",str(execution_receipt.get("resolved_permission_rules_sha256","")))):
        raise BenchmarkContractError("analysis-worker execution receipt is not trustworthy")
    run=run_dir.resolve(strict=True); context_resolved=context_path.resolve(strict=True)
    try: context_resolved.relative_to(run)
    except ValueError as exc: raise BenchmarkContractError("CONTEXT must be a managed Run artifact") from exc
    raw_context=_load_json(context_resolved); payload=raw_context.get("payload",raw_context); candidate=payload.get("candidate") if isinstance(payload,dict) else None
    if not isinstance(candidate,dict) or payload.get("candidate_sha256")!=_canonical_hash(candidate):
        raise BenchmarkContractError("invalid bound CONTEXT candidate")
    actual=execution_receipt.get("artifact_bindings")
    compact=candidate.get("compact_context")
    compact_mode=(isinstance(actual,list) and len(actual)==1 and actual[0].get("name")=="COMPACT_CONTEXT.json")
    expected_execution_agent="analysis-leaf" if compact_mode else "analysis-worker"
    if (execution_receipt.get("logical_role")!="analysis-worker"
            or execution_receipt.get("execution_agent")!=expected_execution_agent):
        raise BenchmarkContractError("analysis-worker execution alias mismatch")
    expected={"name":"COMPACT_CONTEXT.json" if compact_mode else "CONTEXT.json",
              "payload_sha256":_canonical_hash(compact if compact_mode else raw_context)}
    if (not isinstance(actual,list) or len(actual)!=1 or {"name":actual[0].get("name"),"payload_sha256":actual[0].get("payload_sha256") }!=expected
            or not re.fullmatch(r"[a-f0-9]{64}",str(actual[0].get("file_sha256","")))):
        raise BenchmarkContractError("analysis-worker CONTEXT binding mismatch")
    telemetry=parse_jsonl_telemetry(jsonl.splitlines(True))
    if (telemetry["parse_errors"] or telemetry["schema_errors"] or telemetry["native_errors"]
            or telemetry["final_finish_reason"]!="stop" or telemetry["truncated"]
            or telemetry["session_ids"]!=[execution_receipt.get("session_id")]):
        raise BenchmarkContractError("invalid analysis-worker native output")
    try: native=json.loads(telemetry["final_text"])
    except json.JSONDecodeError as exc: raise BenchmarkContractError("analysis-worker final text must be fragment JSON") from exc
    if execution_receipt.get("output_payload_sha256")!=_canonical_hash(native):
        raise BenchmarkContractError("analysis-worker output payload binding mismatch")
    pack=candidate.get("context_pack")
    if compact_mode:
        from runtime import compact_protocol
        try: fragment=compact_protocol.expand_native(native,compact,candidate.get("ordinal_map"),pack)
        except compact_protocol.CompactProtocolError as exc: raise BenchmarkContractError(str(exc)) from exc
    else: fragment=native
    if (not isinstance(fragment,dict) or not isinstance(pack,dict) or fragment.get("schema_version")!="2.0"
            or fragment.get("worker_instance")!="analysis-worker" or fragment.get("run_id")!=pack.get("run_id")
            or fragment.get("fragment_id")!=pack.get("fragment_id") or fragment.get("context_pack_sha256")!=_canonical_hash(pack)
            or fragment.get("obligation_ids")!=pack.get("obligation_ids")
            or fragment.get("skill_receipt_ids")!=[row.get("receipt_id") for row in pack.get("skill_receipts",[])]):
        raise BenchmarkContractError("analysis-worker fragment/context binding mismatch")
    return fragment,candidate,telemetry,execution_receipt

def write_isolated_worker_fragment(run_dir:Path,context_path:Path,execution:TrustedRoleExecution) -> Path:
    """Extract a signed worker final fragment into the Run import boundary."""
    fragment,candidate,_,execution_receipt=_validated_worker_execution(run_dir,context_path,execution)
    run=run_dir.resolve(strict=True); target=run/"tmp"/("evaluator-worker-"+fragment["fragment_id"]+".json"); target.parent.mkdir(parents=True,exist_ok=True)
    _,execution_hash=_persist_execution_attestation(run,execution)
    native=json.loads(parse_jsonl_telemetry(execution._trusted_payload()[1].splitlines(True))["final_text"])
    if candidate.get("adapter_version") is not None:
        native_path=run/"internal/compact-native-outputs"/(fragment["fragment_id"]+".json")
        native_envelope={"artifact_type":"compact_native_output","schema_version":"1.0","fragment_id":fragment["fragment_id"],"native":native}
        native_path.parent.mkdir(parents=True,exist_ok=True); native_encoded=(json.dumps(native_envelope,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode()
        if native_path.exists() and native_path.read_bytes()!=native_encoded: raise BenchmarkContractError("compact native output conflict")
        if not native_path.exists():
            with tempfile.NamedTemporaryFile("wb",dir=native_path.parent,delete=False) as handle:
                handle.write(native_encoded);handle.flush();os.fsync(handle.fileno());temp=Path(handle.name)
            os.replace(temp,native_path);os.chmod(native_path,0o400)
        adapter={"artifact_type":"compact_adapter_receipt","schema_version":"1.0","fragment_id":fragment["fragment_id"],
                 "native_output_sha256":_canonical_hash(native),"adapter_version":candidate["adapter_version"],
                 "ordinal_map_sha256":candidate["ordinal_map_sha256"],"expanded_fragment_sha256":_canonical_hash(fragment),
                 "execution_receipt_sha256":execution_hash}
        adapter_path=run/"internal/compact-adapter-receipts"/(fragment["fragment_id"]+".json")
        adapter_path.parent.mkdir(parents=True,exist_ok=True)
        if adapter_path.exists() and _load_json(adapter_path)!=adapter: raise BenchmarkContractError("compact adapter receipt conflict")
        if not adapter_path.exists():
            with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=adapter_path.parent,delete=False) as handle:
                json.dump(adapter,handle,ensure_ascii=False,sort_keys=True,separators=(",",":")); handle.write("\n"); handle.flush(); os.fsync(handle.fileno()); temp=Path(handle.name)
            os.replace(temp,adapter_path); os.chmod(adapter_path,0o400)
    if target.exists():
        if _load_json(target)!=fragment: raise BenchmarkContractError("worker fragment output conflict")
        return target
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=target.parent,delete=False) as handle:
        json.dump(fragment,handle,ensure_ascii=False,indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno()); temp=Path(handle.name)
    os.replace(temp,target); os.chmod(target,0o400); return target

def write_native_runner_telemetry(run_dir:Path,fragment_path:Path,context_path:Path,
                                  execution:TrustedRoleExecution) -> Path:
    """Persist telemetry only from a signed, context-bound isolated worker execution."""
    fragment,candidate,telemetry,execution_receipt=_validated_worker_execution(run_dir,context_path,execution)
    run=run_dir.resolve(strict=True); fragment_resolved=fragment_path.resolve(strict=True)
    try: fragment_resolved.relative_to(run)
    except ValueError as exc: raise BenchmarkContractError("fragment must be a managed Run artifact") from exc
    raw=_load_json(fragment_resolved); managed=raw.get("payload",raw)
    if managed!=fragment: raise BenchmarkContractError("managed fragment differs from signed worker output")
    raw_context=_load_json(context_path.resolve(strict=True))
    _,execution_hash=_persist_execution_attestation(run,execution)
    receipt={"artifact_type":"runner_telemetry","schema_version":"1.0","run_id":fragment["run_id"],
             "fragment_id":fragment["fragment_id"],"model":DEEPSEEK_MODEL,
             "candidate_sha256":_canonical_hash(candidate),"fragment_sha256":_canonical_hash(fragment),
             "context_sha256":_canonical_hash(raw_context),"session_id":execution_receipt["session_id"],
             "execution_receipt_sha256":execution_hash,
             "input_tokens":telemetry["max_step_input_tokens"],"output_tokens":telemetry["max_step_output_tokens"],
             "finish_reason":"stop","valid_json":True,"captured_by":"opencode-runner"}
    target=run/"internal/telemetry"/(fragment["fragment_id"]+".json"); target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if _load_json(target)!=receipt: raise BenchmarkContractError("telemetry receipt conflict")
        return target
    with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=target.parent,delete=False) as handle:
        json.dump(receipt,handle,ensure_ascii=False,indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno()); temp=Path(handle.name)
    os.replace(temp,target); os.chmod(target,0o400)
    return target

def write_native_semantic_assessment(run_dir:Path, claim:dict[str,Any], facts:list[dict[str,Any]],
                                     execution:TrustedRoleExecution) -> Path:
    """Evaluator-owned writer for the independent auditor's native assessment."""
    if not isinstance(execution,TrustedRoleExecution):
        raise BenchmarkContractError("trusted auditor execution receipt required")
    execution_receipt,jsonl=execution._trusted_payload()
    if (execution_receipt.get("captured_by")!="evaluator" or execution_receipt.get("agent")!="auditor"
            or execution_receipt.get("opencode_version")!="1.18.4" or execution_receipt.get("passed") is not True
            or execution_receipt.get("failures")!=[] or not re.fullmatch(r"[a-f0-9]{64}",str(execution_receipt.get("cwd_manifest_sha256","")))
            or not re.fullmatch(r"[a-f0-9]{64}",str(execution_receipt.get("command_sha256","")))
            or not re.fullmatch(r"[a-f0-9]{64}",str(execution_receipt.get("resolved_config_sha256","")))
            or not re.fullmatch(r"[a-f0-9]{64}",str(execution_receipt.get("resolved_permission_rules_sha256","")))
            or execution_receipt.get("stdout_sha256")!=sha256(jsonl.encode()).hexdigest()):
        raise BenchmarkContractError("auditor execution receipt is not trustworthy")
    claim_id=claim.get("contribution_id",claim.get("risk_id")); keys=claim.get("fact_keys")
    if not isinstance(claim_id,str) or not isinstance(keys,list) or not keys: raise BenchmarkContractError("invalid semantic claim")
    fact_map={(x["obligation_id"],x["inventory_id"],x["line_start"],x["line_count"]):x for x in facts}
    try: excerpts=[fact_map[tuple(key)]["excerpt_sha256"] for key in keys]
    except (KeyError,TypeError) as exc: raise BenchmarkContractError("semantic claim fact binding missing") from exc
    expected_artifacts=[{"name":"CLAIM.json","payload_sha256":_canonical_hash(claim)},
                        {"name":"FACTS.json","payload_sha256":_canonical_hash(facts)}]
    actual=execution_receipt.get("artifact_bindings")
    if (not isinstance(actual,list) or [{"name":row.get("name"),"payload_sha256":row.get("payload_sha256")} for row in actual]!=expected_artifacts
            or any(not re.fullmatch(r"[a-f0-9]{64}",str(row.get("file_sha256",""))) for row in actual)):
        raise BenchmarkContractError("auditor execution artifact binding mismatch")
    telemetry=parse_jsonl_telemetry(jsonl.splitlines(True))
    if (telemetry["parse_errors"] or telemetry["schema_errors"] or telemetry["native_errors"]
            or telemetry["final_finish_reason"]!="stop" or telemetry["truncated"]
            or telemetry["session_ids"]!=[execution_receipt.get("session_id")]):
        raise BenchmarkContractError("invalid auditor native JSONL")
    try: decision=json.loads(telemetry["final_text"])
    except json.JSONDecodeError as exc: raise BenchmarkContractError("auditor final text must be JSON") from exc
    if set(decision)!={"supported","reason"} or not isinstance(decision["supported"],bool) or not isinstance(decision["reason"],str) or len(decision["reason"].strip())<8:
        raise BenchmarkContractError("invalid semantic assessment decision")
    if execution_receipt.get("output_payload_sha256")!=_canonical_hash(decision):
        raise BenchmarkContractError("auditor output payload binding mismatch")
    run=run_dir.resolve(strict=True); _,execution_hash=_persist_execution_attestation(run,execution)
    canonical={k:claim[k] for k in sorted(claim) if k not in {"contribution_id","risk_id"}}
    receipt={"artifact_type":"semantic_assessment","schema_version":"1.0","claim_id":claim_id,
             "claim_sha256":_canonical_hash(canonical),"fact_keys":keys,"source_excerpt_sha256s":excerpts,
             "supported":decision["supported"],"reason":decision["reason"],"auditor_telemetry":{
                 "model":DEEPSEEK_MODEL,"input_tokens":telemetry["max_step_input_tokens"],
                 "output_tokens":telemetry["max_step_output_tokens"],"finish_reason":"stop","valid_json":True,
                 "captured_by":"opencode-runner","session_id":execution_receipt["session_id"],
                 "execution_receipt_sha256":execution_hash}}
    target=run/"internal/semantic-assessments"/(claim_id+".json"); target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and _load_json(target)!=receipt: raise BenchmarkContractError("semantic assessment conflict")
    if not target.exists():
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=target.parent,delete=False) as handle:
            json.dump(receipt,handle,ensure_ascii=False,indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno()); temp=Path(handle.name)
        os.replace(temp,target); os.chmod(target,0o400)
    return target

def write_native_semantic_assessment_batch(run_dir:Path, batch:dict[str,Any],
                                           execution:TrustedRoleExecution) -> list[Path]:
    """Project one signed compact auditor batch into exact per-claim assessments."""
    from runtime import compact_protocol
    if (not isinstance(batch,dict) or set(batch)!={"v","claims"} or batch.get("v")!=1
            or not isinstance(batch.get("claims"),list) or not 1<=len(batch["claims"])<=compact_protocol.AUDITOR_CLAIM_LIMIT):
        raise BenchmarkContractError("invalid semantic audit batch")
    if not isinstance(execution,TrustedRoleExecution): raise BenchmarkContractError("trusted auditor execution receipt required")
    receipt,jsonl=execution._trusted_payload(); actual=receipt.get("artifact_bindings")
    expected={"name":"SEMANTIC_BATCH.json","payload_sha256":_canonical_hash(batch)}
    if (receipt.get("agent")!="auditor" or receipt.get("logical_role")!="auditor"
            or receipt.get("execution_agent")!="audit-leaf"
            or receipt.get("passed") is not True or receipt.get("failures")!=[]
            or not isinstance(actual,list) or len(actual)!=1
            or {"name":actual[0].get("name"),"payload_sha256":actual[0].get("payload_sha256")}!=expected):
        raise BenchmarkContractError("semantic audit batch execution binding mismatch")
    telemetry=parse_jsonl_telemetry(jsonl.splitlines(True))
    try: native=json.loads(telemetry["final_text"])
    except (json.JSONDecodeError,TypeError) as exc: raise BenchmarkContractError("semantic batch output must be JSON") from exc
    if (len(compact_protocol.canonical_bytes(native))>compact_protocol.NATIVE_OUTPUT_BYTE_LIMIT
            or not isinstance(native,dict) or set(native)!={"v","a"} or native.get("v")!=1
            or not isinstance(native.get("a"),list) or len(native["a"])!=len(batch["claims"])
            or receipt.get("output_payload_sha256")!=_canonical_hash(native)):
        raise BenchmarkContractError("semantic batch native closure is invalid")
    decisions={}
    for row in native["a"]:
        if (not isinstance(row,list) or len(row)!=3 or type(row[0]) is not int or type(row[1]) is not bool
                or not isinstance(row[2],str) or not 8<=len(row[2].encode())<=32 or row[0] in decisions):
            raise BenchmarkContractError("semantic batch decision is invalid")
        decisions[row[0]]=(row[1],row[2])
    expected_ordinals=[row.get("ordinal") for row in batch["claims"] if isinstance(row,dict)]
    if sorted(decisions)!=expected_ordinals or expected_ordinals!=list(range(len(batch["claims"]))):
        raise BenchmarkContractError("semantic batch ordinal closure is invalid")
    run=run_dir.resolve(strict=True); _,execution_hash=_persist_execution_attestation(run,execution); targets=[]
    for entry in batch["claims"]:
        if set(entry)!={"ordinal","claim","facts"} or not isinstance(entry["claim"],dict) or not isinstance(entry["facts"],list):
            raise BenchmarkContractError("semantic batch claim projection is invalid")
        ordinal=entry["ordinal"]; claim=entry["claim"]; facts=entry["facts"]; supported,reason=decisions[ordinal]
        claim_id=claim.get("contribution_id",claim.get("risk_id")); keys=claim.get("fact_keys")
        fact_map={(x.get("obligation_id"),x.get("inventory_id"),x.get("line_start"),x.get("line_count")):x for x in facts if isinstance(x,dict)}
        try: excerpts=[fact_map[tuple(key)]["excerpt_sha256"] for key in keys]
        except (KeyError,TypeError) as exc: raise BenchmarkContractError("semantic batch fact binding missing") from exc
        canonical={k:claim[k] for k in sorted(claim) if k not in {"contribution_id","risk_id"}}
        value={"artifact_type":"semantic_assessment","schema_version":"1.0","claim_id":claim_id,
               "claim_sha256":_canonical_hash(canonical),"fact_keys":keys,"source_excerpt_sha256s":excerpts,
               "supported":supported,"reason":reason,"auditor_telemetry":{"model":DEEPSEEK_MODEL,
               "input_tokens":telemetry["max_step_input_tokens"],"output_tokens":telemetry["max_step_output_tokens"],
               "finish_reason":"stop","valid_json":True,"captured_by":"opencode-runner",
               "session_id":receipt["session_id"],"execution_receipt_sha256":execution_hash}}
        target=run/"internal/semantic-assessments"/(claim_id+".json"); target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and _load_json(target)!=value: raise BenchmarkContractError("semantic assessment conflict")
        if not target.exists():
            with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=target.parent,delete=False) as handle:
                json.dump(value,handle,ensure_ascii=False,indent=2);handle.write("\n");handle.flush();os.fsync(handle.fileno());temp=Path(handle.name)
            os.replace(temp,target);os.chmod(target,0o400)
        targets.append(target)
    return targets


def write_native_report_audit(run_dir:Path, artifacts:Mapping[str,Any],
                              execution:TrustedRoleExecution) -> Path:
    """Materialize an independently executed, fixed-artifact report opinion."""
    if set(artifacts) != set(_REPORT_AUDIT_ARTIFACTS) or not isinstance(execution, TrustedRoleExecution):
        raise BenchmarkContractError("trusted fixed-artifact auditor execution required")
    execution_receipt, jsonl = execution._trusted_payload()
    if (execution_receipt.get("agent") != "auditor" or execution_receipt.get("passed") is not True
            or execution_receipt.get("failures") != []
            or execution_receipt.get("stdout_sha256") != sha256(jsonl.encode()).hexdigest()):
        raise BenchmarkContractError("report auditor execution receipt is not trustworthy")
    expected_bindings = [
        {"name": name, "payload_sha256": _canonical_hash(value)}
        for name, value in sorted(artifacts.items())
    ]
    actual = execution_receipt.get("artifact_bindings")
    if (not isinstance(actual, list)
            or [{"name": row.get("name"), "payload_sha256": row.get("payload_sha256")} for row in actual]
               != expected_bindings):
        raise BenchmarkContractError("report auditor artifact binding mismatch")
    telemetry = parse_jsonl_telemetry(jsonl.splitlines(True))
    if (telemetry["parse_errors"] or telemetry["schema_errors"] or telemetry["native_errors"]
            or telemetry["final_finish_reason"] != "stop" or telemetry["truncated"]
            or telemetry["session_ids"] != [execution_receipt.get("session_id")]):
        raise BenchmarkContractError("invalid report auditor native JSONL")
    try:
        opinion = json.loads(telemetry["final_text"])
    except json.JSONDecodeError as exc:
        raise BenchmarkContractError("report auditor final text must be JSON") from exc
    from runtime import runctl
    try:
        runctl.validate(opinion, "audit-opinion.schema.json")
    except runctl.RunCtlError as exc:
        raise BenchmarkContractError("report auditor opinion schema mismatch") from exc
    report_path = run_dir / "internal/report-model.json"
    if (_load_json(report_path) != artifacts["REPORT_MODEL.json"]
            or opinion.get("audited_sha256") != sha256(report_path.read_bytes()).hexdigest()
            or execution_receipt.get("output_payload_sha256") != _canonical_hash(opinion)):
        raise BenchmarkContractError("report auditor opinion/report binding mismatch")
    run = run_dir.resolve(strict=True)
    _persist_execution_attestation(run, execution, "final-audit-execution-receipts")
    target = run / "tmp/evaluator-report-audit.json"; target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _load_json(target) != opinion:
            raise BenchmarkContractError("report auditor opinion conflict")
        return target
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        json.dump(opinion, handle, ensure_ascii=False, indent=2); handle.write("\n")
        handle.flush(); os.fsync(handle.fileno()); temporary = Path(handle.name)
    os.replace(temporary, target); os.chmod(target, 0o400)
    return target


def normalize_candidate_output(candidate: dict[str, Any]) -> dict[str, Any]:
    """Convert a candidate report into a neutral evaluator-owned shape.

    The function intentionally does not inspect hidden answers or award any
    semantic credit.  Human/independent judging supplies dimension scores.
    """
    if not isinstance(candidate, dict):
        raise BenchmarkContractError("candidate output must be an object")
    findings = candidate.get("risk_cards", candidate.get("findings", candidate.get("risks", [])))
    if not isinstance(findings, list):
        raise BenchmarkContractError("candidate findings must be a list")
    normalized: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, dict):
            continue
        normalized.append({
            "id": str(finding.get("risk_id", finding.get("id", f"finding-{index}"))),
            "title": str(finding.get("title", "")),
            "severity": str(finding.get("severity", "")),
            "source_evidence": finding.get("source_evidence", finding.get("evidence", [])),
            "blackbox_tests": finding.get("test_cases", finding.get("blackbox_tests", [])),
        })
    def list_field(*names: str) -> list[Any]:
        for name in names:
            value = candidate.get(name)
            if value is not None:
                return value if isinstance(value, list) else [value]
        return []
    return {
        "schema_version": "1.0",
        "claims": list_field("claims"),
        "evidence": list_field("evidence", "source_evidence"),
        "flow_chains": list_field("flow_chains", "flows"),
        "state_chains": list_field("state_chains", "state_changes"),
        "resource_chains": list_field("resource_chains", "resource_lifecycle"),
        "error_chains": list_field("error_chains", "error_propagation"),
        "risks": normalized,
        "findings": normalized,
        "disconfirming_checks": list_field("disconfirming_checks", "counterexamples"),
        "scenarios": list_field("scenarios", "scenario_candidates"),
        "cases": list_field("cases", "test_cases", "test_flows"),
        "na": list_field("na", "not_applicable"),
        "dispositions": list_field("dispositions"),
        "raw_finding_count": len(findings),
        "evaluator_review_required": bool(candidate.get("evaluator_review_required", False)),
        "unparsed_sections": list_field("unparsed_sections"),
    }


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}")
_EVIDENCE_REF = re.compile(
    r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:c|cc|cpp|cxx|h|hpp|py|sh|rs)"
    r"(?:(?::\d+(?:-\d+)?)|(?:#L\d+(?:-L?\d+)?))"
)


def _section_category(title: str) -> str | None:
    lowered = title.casefold()
    categories = (
        ("disconfirming_checks", ("disconfirm", "counterexample", "反证", "排除", "免疫")),
        ("scenarios", ("scenario", "测试场景", "场景候选")),
        ("cases", ("test case", "测试用例", "黑盒用例", "用例")),
        ("na", ("not applicable", "n/a", "不适用")),
        ("dispositions", ("disposition", "处置", "覆盖审计", "覆盖门禁")),
        ("risks", ("risk", "sfmea", "风险", "失效模式")),
        ("resource_chains", ("resource", "资源", "所有权")),
        ("state_chains", ("state", "状态")),
        ("error_chains", ("error", "exception", "异常传播", "错误传播", "恢复路径")),
        ("flow_chains", ("flow", "流程", "调用链")),
        ("evidence", ("evidence", "证据", "源码位置")),
        ("claims", ("claim", "结论", "摘要", "概述", "代码说明")),
    )
    for category, markers in categories:
        if any(marker in lowered for marker in markers):
            return category
    return None


def _parse_table(lines: list[str], start: int) -> tuple[list[dict[str, str]], int]:
    if start + 1 >= len(lines) or "|" not in lines[start] or not _TABLE_SEPARATOR.match(lines[start + 1]):
        return [], start
    headers = [cell.strip() for cell in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    index = start + 2
    while index < len(lines) and "|" in lines[index] and lines[index].strip():
        cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        cells.extend([""] * (len(headers) - len(cells)))
        rows.append(dict(zip(headers, cells[:len(headers)])))
        index += 1
    return rows, index - 1


def _markdown_candidate(raw: str) -> dict[str, Any]:
    """Neutrally extract structured Markdown without assigning rubric credit."""
    candidate: dict[str, Any] = {
        name: [] for name in (
            "claims", "evidence", "flow_chains", "state_chains", "resource_chains",
            "error_chains", "risks", "disconfirming_checks", "scenarios", "cases",
            "na", "dispositions",
        )
    }
    unparsed: list[str] = []
    recognized: set[str] = set()
    structured_blocks: list[dict[str, Any]] = []
    for match in re.finditer(r"```(?:json)?\s*\n(.*?)\n```", raw, re.S | re.I):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            structured_blocks.append(value)
    lines = raw.splitlines()
    current_title = "Preamble"
    current_category: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = _HEADING.match(line)
        if heading:
            current_title = heading.group(2).strip()
            current_category = _section_category(current_title)
            if current_category:
                recognized.add(current_title)
            elif len(heading.group(1)) > 1:
                unparsed.append(current_title)
            index += 1
            continue
        table, end = _parse_table(lines, index)
        if table:
            if current_category:
                if current_category == "risks":
                    for row_index, row in enumerate(table, start=1):
                        row_text = " | ".join(value for value in row.values() if value)
                        candidate["risks"].append({
                            "id": next((value for key, value in row.items() if key.casefold() in {"id", "risk_id", "风险id", "编号"}), f"md-risk-{row_index}"),
                            "title": next((value for key, value in row.items() if any(marker in key.casefold() for marker in ("title", "风险", "失效", "failure"))), row_text),
                            "severity": next((value for key, value in row.items() if any(marker in key.casefold() for marker in ("severity", "严重"))), ""),
                            "source_evidence": _EVIDENCE_REF.findall(row_text),
                            "table": row,
                        })
                else:
                    candidate[current_category].extend({"section": current_title, "table": row} for row in table)
            else:
                unparsed.append(f"{current_title}:table")
            index = end + 1
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("```") and current_category:
            text = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", stripped)
            if text and not _TABLE_SEPARATOR.match(text):
                if current_category == "risks":
                    candidate["risks"].append({"id": f"md-risk-{len(candidate['risks']) + 1}", "title": text, "severity": "", "source_evidence": _EVIDENCE_REF.findall(text)})
                else:
                    candidate[current_category].append({"section": current_title, "text": text})
        index += 1
    candidate["evidence"].extend({"ref": ref} for ref in dict.fromkeys(_EVIDENCE_REF.findall(raw)))
    for block in structured_blocks:
        neutral = normalize_candidate_output(block)
        for name in (
            "claims", "evidence", "flow_chains", "state_chains", "resource_chains",
            "error_chains", "risks", "disconfirming_checks", "scenarios", "cases",
            "na", "dispositions",
        ):
            candidate[name].extend(neutral[name])
    substantive_unparsed = sorted(set(title for title in unparsed if title != "Preamble"))
    candidate["unparsed_sections"] = substantive_unparsed
    candidate["evaluator_review_required"] = bool(substantive_unparsed) or not bool(recognized or structured_blocks)
    return candidate


def _candidate_from_text(text: str) -> tuple[dict[str, Any], str]:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S | re.I)
    json_text = fenced.group(1) if fenced else stripped
    try:
        value = json.loads(json_text)
    except json.JSONDecodeError:
        return _markdown_candidate(stripped), "markdown"
    if not isinstance(value, dict):
        raise BenchmarkContractError("candidate final JSON must be an object")
    return value, "json"


def seal_candidate_output(raw: str, candidate: str, track: str, case_id: str, *, format: str) -> dict[str, Any]:
    """Adapt raw PANGEA JSON/JSONL or Fuse Markdown/JSON and bind its digest."""
    if candidate not in load_frozen_config()["candidates"]:
        raise BenchmarkContractError("unknown candidate for raw output")
    try:
        if format == "json":
            parsed = json.loads(raw)
        elif format == "jsonl":
            telemetry = parse_jsonl_telemetry(raw.splitlines(True))
            if telemetry["parse_errors"] or telemetry["schema_errors"]:
                raise BenchmarkContractError("JSONL adapter parse failure")
            if telemetry["truncated"]:
                raise BenchmarkContractError("JSONL adapter refuses truncated output")
            if not telemetry["final_text"].strip():
                raise BenchmarkContractError("JSONL adapter found no native final text")
            parsed, adapted_from = _candidate_from_text(telemetry["final_text"])
        elif format == "markdown" and candidate == "fuse":
            parsed = _markdown_candidate(raw)
            adapted_from = "markdown"
        else:
            raise BenchmarkContractError("unsupported candidate output adapter")
    except json.JSONDecodeError as exc:
        raise BenchmarkContractError("candidate JSON adapter parse failure") from exc
    if not isinstance(parsed, dict):
        raise BenchmarkContractError("candidate adapter must yield an object")
    neutral = normalize_candidate_output(parsed)
    return {
        "candidate": candidate,
        "track": track,
        "case_id": case_id,
        "raw_sha256": sha256(raw.encode()).hexdigest(),
        "adapter": locals().get("adapted_from", format),
        "neutral": neutral,
        "neutral_sha256": sha256(json.dumps(neutral, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
        "score_eligible": not neutral["evaluator_review_required"],
    }


def apply_adapter_review(candidate_output: dict[str, Any], review_path: Path) -> dict[str, Any]:
    """Apply an independently stored adapter-resolution receipt.

    A parser-marked output remains unscoreable until an evaluator outside this
    workspace binds a resolved neutral form to the original raw digest and
    explicitly disposes every unparsed section.
    """
    if review_path.is_symlink() or not review_path.is_file():
        raise BenchmarkContractError("adapter review must be a regular non-symlink file")
    try:
        review_path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise BenchmarkContractError("adapter review must be stored outside the candidate workspace")
    _validate_candidate_seal(candidate_output, require_scoreable=False)
    if candidate_output["neutral"].get("evaluator_review_required") is not True:
        raise BenchmarkContractError("adapter review is only valid for parser-marked output")
    review = _load_json(review_path)
    required = {
        "schema_version", "review_kind", "candidate", "track", "case_id",
        "raw_sha256", "reviewer", "verdict", "resolved_sections",
        "resolution_note", "resolved_neutral",
    }
    if set(review) != required:
        raise BenchmarkContractError("adapter review fields must exactly match the sealed review schema")
    expected_binding = {
        "candidate": candidate_output["candidate"],
        "track": candidate_output["track"],
        "case_id": candidate_output["case_id"],
        "raw_sha256": candidate_output["raw_sha256"],
    }
    if any(review[name] != value for name, value in expected_binding.items()):
        raise BenchmarkContractError("adapter review is not bound to this candidate output")
    if review["schema_version"] != "1.0" or review["review_kind"] != "neutral-adapter-resolution" or review["verdict"] != "resolved":
        raise BenchmarkContractError("adapter review must be an explicit resolved neutral-adapter-resolution")
    if not isinstance(review["reviewer"], str) or not review["reviewer"].strip():
        raise BenchmarkContractError("adapter review requires an independent reviewer identity")
    if not isinstance(review["resolution_note"], str) or not review["resolution_note"].strip():
        raise BenchmarkContractError("adapter review requires a non-empty resolution note")
    expected_sections = sorted(set(candidate_output["neutral"].get("unparsed_sections", [])))
    if not isinstance(review["resolved_sections"], list) or sorted(set(review["resolved_sections"])) != expected_sections:
        raise BenchmarkContractError("adapter review must dispose every unparsed section exactly")
    resolved_source = review["resolved_neutral"]
    if not isinstance(resolved_source, dict):
        raise BenchmarkContractError("adapter review resolved_neutral must be an object")
    if resolved_source.get("evaluator_review_required") is not False or resolved_source.get("unparsed_sections") != []:
        raise BenchmarkContractError("resolved neutral output must clear review and unparsed-section flags")
    resolved = normalize_candidate_output(resolved_source)
    if resolved["evaluator_review_required"] or resolved["unparsed_sections"]:
        raise BenchmarkContractError("adapter review did not produce a scoreable neutral output")
    result = dict(candidate_output)
    result["neutral"] = resolved
    result["neutral_sha256"] = sha256(json.dumps(resolved, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    result["score_eligible"] = True
    result["adapter_review_receipt"] = {
        "review_sha256": sha256(review_path.read_bytes()).hexdigest(),
        "reviewer": review["reviewer"],
        "verdict": review["verdict"],
        "loaded_from_outside_workspace": True,
    }
    return result


def _validate_candidate_seal(candidate_output: dict[str, Any], *, require_scoreable: bool) -> None:
    required = {"candidate", "track", "case_id", "raw_sha256", "adapter", "neutral", "neutral_sha256", "score_eligible"}
    if not isinstance(candidate_output, dict) or not required.issubset(candidate_output):
        raise BenchmarkContractError("scorer requires a sealed candidate output")
    neutral = candidate_output["neutral"]
    if not isinstance(neutral, dict):
        raise BenchmarkContractError("sealed candidate neutral output must be an object")
    observed = sha256(json.dumps(neutral, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if observed != candidate_output["neutral_sha256"]:
        raise BenchmarkContractError("sealed candidate neutral digest mismatch")
    if neutral.get("evaluator_review_required") is True and candidate_output.get("score_eligible") is not False:
        raise BenchmarkContractError("review-required adapter output cannot be score eligible")
    if require_scoreable and candidate_output.get("score_eligible") is not True:
        raise BenchmarkContractError("candidate output is not scoreable until independent adapter review resolves it")


def score_dimensions(oracle: SealedOracle, awarded: dict[str, float], *, candidate_output: dict[str, Any]) -> dict[str, Any]:
    """Score six evaluator credits; Oracle supplies criteria, never weights."""
    if not isinstance(oracle, SealedOracle):
        raise BenchmarkContractError("scorer only accepts a SealedOracle loader receipt")
    _validate_candidate_seal(candidate_output, require_scoreable=True)
    if "scoring" in oracle.payload or "weights" in oracle.payload:
        raise BenchmarkContractError("sealed Oracle may not override frozen score weights")
    weights = load_frozen_config()["scorecard"]["weights"]
    if set(awarded) != set(weights):
        raise BenchmarkContractError("all and only six frozen score dimensions are required")
    rows: list[dict[str, Any]] = []
    for dimension, weight in weights.items():
        raw_credit = awarded[dimension]
        if isinstance(raw_credit, bool):
            raise BenchmarkContractError("credits must be finite numbers in [0, 1]")
        credit = float(raw_credit)
        if not math.isfinite(credit) or not 0 <= credit <= 1:
            raise BenchmarkContractError("credits must be in [0, 1]")
        rows.append({"dimension": dimension, "weight": weight, "credit": credit})
    return {
        "score": round(sum(row["weight"] * row["credit"] for row in rows), 4),
        "dimensions": rows,
        "oracle_receipt": oracle.receipt,
        "candidate_receipt": {
            "raw_sha256": candidate_output["raw_sha256"],
            "neutral_sha256": candidate_output["neutral_sha256"],
            "adapter_review_receipt": candidate_output.get("adapter_review_receipt"),
        },
    }


def _gate_number(name: str, value: Any, *, low: float = 0, high: float = 100) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise BenchmarkContractError(f"gate metric {name} must be finite numeric")
    result = float(value)
    if not low <= result <= high:
        raise BenchmarkContractError(f"gate metric {name} outside [{low}, {high}]")
    return result


def evaluate_gates(metrics: dict[str, float], fuse_score: float, paired_ci_lower: float, core_win_rate: float) -> dict[str, bool]:
    """Evaluate only the complete frozen gate set; missing/NaN/range drift fails."""
    thresholds = load_frozen_config()["scorecard"]["thresholds"]
    absolute_thresholds = thresholds["absolute"]
    required = set(absolute_thresholds["minimum"]) | set(absolute_thresholds["maximum"]) | set(absolute_thresholds["must_be_false"])
    if set(metrics) != required:
        raise BenchmarkContractError(f"gate metrics must exactly match frozen set; missing={sorted(required - set(metrics))}, extra={sorted(set(metrics) - required)}")
    numeric: dict[str, float] = {}
    for name in set(absolute_thresholds["minimum"]) | set(absolute_thresholds["maximum"]):
        numeric[name] = _gate_number(name, metrics[name])
    for name in absolute_thresholds["must_be_false"]:
        if not isinstance(metrics[name], bool):
            raise BenchmarkContractError(f"gate metric {name} must be boolean")
    fuse = _gate_number("fuse_score", fuse_score)
    ci_lower = _gate_number("paired_ci_lower", paired_ci_lower, low=-100, high=100)
    win_rate = _gate_number("core_win_rate", core_win_rate)
    absolute = all(numeric[name] >= value for name, value in absolute_thresholds["minimum"].items())
    absolute = absolute and all(numeric[name] <= value for name, value in absolute_thresholds["maximum"].items())
    absolute = absolute and all(metrics[name] is False for name in absolute_thresholds["must_be_false"])
    delta = numeric["score"] - fuse
    at_least = thresholds["versus_fuse"]["at_least"]
    noninferior = (
        delta >= at_least["mean_score_delta"]
        and ci_lower >= at_least["score_delta_lower_ci"]
        and numeric["hard_gate_regression"] <= at_least["hard_gate_regression"]
    )
    exceeds_gate = thresholds["versus_fuse"]["exceeds"]
    exceeds = (
        delta >= exceeds_gate["mean_score_delta"]
        and ci_lower > exceeds_gate["score_delta_lower_ci_exclusive"]
        and win_rate >= exceeds_gate["core_case_win_rate"]
        and numeric["hard_gate_regression"] <= exceeds_gate["hard_gate_regression"]
    )
    return {"absolute": absolute, "at_least_fuse": absolute and noninferior, "exceeds_fuse": absolute and exceeds}


def load_sealed_oracle(path: Path) -> SealedOracle:
    """Load an evaluator-private oracle only when it is outside this workspace."""
    if path.is_symlink() or not path.is_file():
        raise BenchmarkContractError("sealed oracle must be a regular non-symlink file")
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        payload = _load_json(path)
        return SealedOracle(payload, {"path_sha256": sha256(path.read_bytes()).hexdigest(), "loaded_from_outside_workspace": True})
    raise BenchmarkContractError("sealed oracle must be stored outside the candidate workspace")
