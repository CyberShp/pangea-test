from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


runtime = read("runtime/evidence_runtime.py")
runtime = runtime.replace("import hashlib\n", "import hashlib\nimport re\n", 1)

# Contract-selected materials must be declared in task_contract.input_refs.
old = '''        decision = item["decision"]
        anchors = item["consumed_anchors"]
        if decision != "selected":
'''
new = '''        decision = item["decision"]
        anchors = item["consumed_anchors"]
        if decision == "selected" and source_ref not in contract_refs:
            raise EvidenceRuntimeError(f"材料 {material_id} 被选择但未声明在任务契约 input_refs")
        if decision != "selected":
'''
runtime = replace_once(runtime, old, new, "selected material contract binding")

# Add deterministic unified-diff parsing before validate_provenance.
marker = '\ndef validate_provenance(payload: dict[str, Any], root: Path, run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:\n'
parser = r'''

def _parse_unified_diff(data: bytes) -> list[dict[str, Any]]:
    """Parse canonical Git unified-diff file paths and hunk headers."""
    text = data.decode("utf-8", errors="replace")
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        match = re.match(r"^diff --git a/(.+) b/(.+)$", raw)
        if match:
            if current is not None:
                files.append(current)
            current = {"path": match.group(2), "hunks": []}
            continue
        if current is None:
            continue
        if raw.startswith("+++ b/"):
            current["path"] = raw[6:]
            continue
        hunk = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
        if hunk:
            current["hunks"].append({
                "old_start": int(hunk.group(1)), "old_count": int(hunk.group(2) or "1"),
                "new_start": int(hunk.group(3)), "new_count": int(hunk.group(4) or "1"),
            })
    if current is not None:
        files.append(current)
    if not files:
        raise EvidenceRuntimeError("固定 MR diff 不包含可解析的 diff --git 文件记录")
    for item in files:
        _safe_posix(item["path"], "MR diff file path")
    return files
'''
runtime = replace_once(runtime, marker, parser + marker, "diff parser")

old_mr = '''        if mr_facts.get("resolved_commits") != expected_commits:
            raise EvidenceRuntimeError("mr_facts.resolved_commits 与任务契约不一致")
        for changed in mr_facts.get("changed_files", []):
            _safe_posix(changed["path"], "MR changed file path")
'''
new_mr = '''        if mr_facts.get("resolved_commits") != expected_commits:
            raise EvidenceRuntimeError("mr_facts.resolved_commits 与任务契约不一致")
        diff_binding = mr_facts.get("diff_artifact")
        expected_relative = "internal/mr.diff"
        if not isinstance(diff_binding, dict) or diff_binding.get("path") != expected_relative:
            raise EvidenceRuntimeError("mr_facts 缺少固定 internal/mr.diff binding")
        diff_path = _under(run_dir / expected_relative, run_dir, "固定 MR diff")
        if not diff_path.is_file():
            raise EvidenceRuntimeError("固定 MR diff 不是普通文件")
        digest = sha256_file(diff_path)
        if diff_binding.get("sha256") != digest or mr_facts.get("diff_sha256") != digest:
            raise EvidenceRuntimeError("mr_facts diff SHA-256 与固定 MR diff 不一致")
        parsed_files = _parse_unified_diff(diff_path.read_bytes())
        declared_files = mr_facts.get("changed_files", [])
        for changed in declared_files:
            _safe_posix(changed["path"], "MR changed file path")
        if declared_files != parsed_files:
            raise EvidenceRuntimeError("mr_facts changed_files/hunks 与固定 MR diff 不一致")
'''
runtime = replace_once(runtime, old_mr, new_mr, "MR diff binding")
write("runtime/evidence_runtime.py", runtime)

schema = json.loads(read("schemas/evidence-provenance.schema.json"))
mr = schema["properties"]["mr_facts"]
required = mr["required"]
required.insert(required.index("diff_sha256"), "diff_artifact")
mr["properties"]["diff_artifact"] = {
    "type": "object", "additionalProperties": False, "required": ["path", "sha256"],
    "properties": {"path": {"const": "internal/mr.diff"},
                   "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
}
mr["properties"]["changed_files"]["items"]["properties"]["hunks"].pop("minItems", None)
write("schemas/evidence-provenance.schema.json", json.dumps(schema, ensure_ascii=False, indent=2) + "\n")

runctl = read("runtime/runctl.py")
runctl = replace_once(
    runctl,
    'EVIDENCE_PROVENANCE_RELATIVE = "internal/evidence-provenance.json"\n',
    'EVIDENCE_PROVENANCE_RELATIVE = "internal/evidence-provenance.json"\nMR_DIFF_RELATIVE = "internal/mr.diff"\n',
    "MR diff constant",
)

# Add byte-safe MR diff staging before evidence staging.
marker = '\ndef stage_evidence_v2(args: argparse.Namespace) -> None:\n'
staging = r'''

def _mr_diff_path(run_dir: Path) -> Path:
    internal = (run_dir / "internal").resolve()
    path = run_dir / MR_DIFF_RELATIVE
    if path.is_symlink() or path.resolve().parent != internal:
        raise RunCtlError("MR diff 不得通过符号链接指向 Run 外部")
    return path.resolve()


def stage_mr_diff_v2(args: argparse.Namespace) -> None:
    """Copy one provider/exported unified diff into the fixed Run artifact."""
    from runtime import data_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入 MR diff")
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-mr-diff-v2 仅用于任务契约生命周期创建的新 Run")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))
    if contract.get("mode") != "mr_regression":
        raise RunCtlError("stage-mr-diff-v2 仅用于 MR 回归")
    if manifest.get("audit", {}).get("status") == "PASS":
        raise RunCtlError("审计 PASS 后不得改写 MR diff")
    source = Path(args.file).expanduser()
    if source.is_symlink() or not source.is_file():
        raise RunCtlError(f"MR diff 输入必须是普通文件: {source}")
    data = source.read_bytes()
    if not data.strip() or b"diff --git " not in data:
        raise RunCtlError("MR diff 输入为空或不包含 diff --git 记录")
    target = _mr_diff_path(run_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno()); temporary = Path(handle.name)
    temporary.replace(target)
    _invalidate_fixed_artifact(_evidence_provenance_path(run_dir))
    _invalidate_fixed_artifact(_analysis_model_path(run_dir))
    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
    digest = _sha256_file(target)
    print(json.dumps({"run_id": args.run_id, "mr_diff": str(target),
                      "diff_artifact": {"path": MR_DIFF_RELATIVE, "sha256": digest},
                      "next_step": "stage-evidence-v2"}, ensure_ascii=False))


'''
runctl = replace_once(runctl, marker, staging + marker, "MR diff staging")

parser_marker = '    evidence2 = sub.add_parser("stage-evidence-v2", help="校验并落盘材料、发现、MR 与源码证据 provenance")\n'
parser_insert = '''    mrdiff2 = sub.add_parser("stage-mr-diff-v2", help="将 MR unified diff 落盘为固定 Run 工件")\n    mrdiff2.add_argument("--run-id", required=True)\n    mrdiff2.add_argument("--file", required=True)\n    mrdiff2.add_argument("--root")\n    mrdiff2.set_defaults(func=stage_mr_diff_v2)\n'''
runctl = replace_once(runctl, parser_marker, parser_insert + parser_marker, "MR diff parser")
write("runtime/runctl.py", runctl)

# Update MR command and reader contracts.
mr_command = read(".opencode/commands/mr-regression.md")
old = "证据门禁：MR facts、diff、changed hunks、材料选择、搜索过程和源码行证据必须先写入 `<evidence-provenance.json>`，并调用"
new = "证据门禁：先调用 `<preflight.python_executable> runtime/runctl.py stage-mr-diff-v2 --run-id <Run ID> --file <provider-exported.diff>` 固定真实 diff；再将 `mr_facts`、`diff_artifact`、changed hunks、材料选择、搜索过程和源码行证据写入 `<evidence-provenance.json>`，并调用"
mr_command = replace_once(mr_command, old, new, "MR command diff staging")
write(".opencode/commands/mr-regression.md", mr_command)

mr_reader = read(".opencode/agents/mr-reader.md")
mr_reader += "\nMR diff 必须由主 Agent 通过 `stage-mr-diff-v2` 固定，`mr_facts.diff_artifact` 和 `diff_sha256` 必须引用该命令返回值；changed_files/hunks 必须与固定 diff 完全一致。\n"
write(".opencode/agents/mr-reader.md", mr_reader)

# Add MR-specific and contract-material negative tests.
tests = read("tests/test_evidence_provenance.py")
insert = r'''
    def activate_mr(self, root: Path, contract_id: str = "mr") -> Path:
        ContractLifecycleTests().prepare(root)
        repo = root / "pangea-data/repositories/driver"
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                                text=True, capture_output=True).stdout.strip()
        self.cli(root, "draft-contract-v2", "--scenario", "mr-regression", "--target", "chap",
                 "--repository", "driver", "--repository-commit", f"driver={commit}",
                 "--mr-url", "https://git.example.invalid/driver/merge_requests/7",
                 "--analysis-depth", "focused", "--contract-id", contract_id)
        self.cli(root, "confirm-contract-v2", "--contract-id", contract_id, "--revision", "1",
                 "--source", "auto_unambiguous", "--materials-status", "unchanged")
        activated = self.cli(root, "activate-contract-v2", "--contract-id", contract_id,
                             "--run-id", contract_id + "-run")
        run_dir = Path(activated["run_dir"])
        repository_runtime.create_snapshot(root, run_dir.name, "driver", commit, "driver")
        return run_dir

    def mr_payload(self, root: Path, run_dir: Path) -> dict:
        diff = root / "mr.diff"
        diff.write_text(
            "diff --git a/driver.c b/driver.c\n"
            "index 1111111..2222222 100644\n"
            "--- a/driver.c\n+++ b/driver.c\n"
            "@@ -1,1 +1,1 @@\n-int entry(void) { return 0; }\n+int entry(void) { return 1; }\n",
            encoding="utf-8",
        )
        staged = self.cli(root, "stage-mr-diff-v2", "--run-id", run_dir.name, "--file", str(diff))
        payload = self.payload(root, run_dir)
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        payload["mr_facts"] = {
            "mr_url": contract["mr_url"], "provider": "test-export",
            "resolved_commits": contract["repository_commits"],
            "diff_artifact": staged["diff_artifact"], "diff_sha256": staged["diff_artifact"]["sha256"],
            "changed_files": [{"path": "driver.c", "hunks": [
                {"old_start": 1, "old_count": 1, "new_start": 1, "new_count": 1}
            ]}],
            "developer_self_test": ["已验证正常连接"], "facts": ["driver.c 返回值发生修改"],
            "inferences": ["可能影响上层状态判断"], "limitations": [],
        }
        return payload

    def test_mr_requires_fixed_diff_and_exact_hunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate_mr(root)
            payload = self.mr_payload(root, run_dir)
            payload["mr_facts"]["changed_files"][0]["hunks"][0]["new_count"] = 2
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("changed_files/hunks", rejected["stderr"])
            payload = self.mr_payload(root, run_dir)
            payload["mr_facts"]["diff_sha256"] = "0" * 64
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("diff SHA-256", rejected["stderr"])

    def test_mr_without_mr_facts_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate_mr(root, "mr-missing")
            payload = self.mr_payload(root, run_dir); payload["mr_facts"] = None
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("mr_facts", rejected["stderr"])

    def test_selected_material_outside_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ContractLifecycleTests.marked_root(root); ContractLifecycleTests.repository(root)
            workspace = data_runtime.ensure_layout(root)
            (workspace / "inbox/extra.md").write_text("# 额外材料\n未在任务契约中声明。\n", encoding="utf-8")
            data_runtime.scan_inbox(root); data_runtime.convert_catalog(root); ContractLifecycleTests.receipt(root)
            run_dir = self.activate_existing_without_input(root)
            catalog_path = workspace / "library/catalog.jsonl"; record = data_runtime._read_jsonl(catalog_path)[0]
            markdown = workspace / record["markdown_path"]; excerpt = b"".join(markdown.read_bytes().splitlines(keepends=True)[:2])
            material = {"material_id": "MAT-X", "source_ref": "extra.md", "source_sha256": record["sha256"],
                        "decision": "selected", "reason": "尝试选择契约外额外材料进行分析",
                        "markdown_path": record["markdown_path"], "markdown_sha256": hashlib.sha256(markdown.read_bytes()).hexdigest(),
                        "consumed_anchors": [{"start_line": 1, "end_line": 2,
                            "excerpt_sha256": hashlib.sha256(excerpt).hexdigest(), "claim": "额外材料内容"}],
                        "limitations": []}
            catalog = {"path": "library/catalog.jsonl", "sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest()}
            rejected = self.stage(root, run_dir, self.payload(root, run_dir, materials=[material], catalog=catalog), expected=2)
            self.assertIn("未声明在任务契约", rejected["stderr"])

    def activate_existing_without_input(self, root: Path) -> Path:
        self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                 "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "extra")
        self.cli(root, "confirm-contract-v2", "--contract-id", "extra", "--revision", "1",
                 "--source", "user_reply", "--materials-status", "unchanged")
        return Path(self.cli(root, "activate-contract-v2", "--contract-id", "extra", "--run-id", "extra-run")["run_dir"])

'''
marker = '\n    def test_analysis_model_must_use_fixed_evidence_ids(self) -> None:\n'
tests = replace_once(tests, marker, "\n" + insert + marker, "MR tests")
write("tests/test_evidence_provenance.py", tests)
