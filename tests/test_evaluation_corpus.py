from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks import stage as public_stage
from evaluation.benchmark import (
    BenchmarkContractError,
    CANDIDATE_SECRET_ASSIGNMENT_KEYS,
    ENVIRONMENT_ALLOWLIST,
    PROVIDER_CREDENTIAL_ENV_KEYS,
    load_corpus_manifest,
    validate_public_bundle,
    write_public_bundle_manifest,
)
from evaluation.corpus import _copy_candidate_tree, _validate_candidate_payload, stage_public_corpus
from evaluation.codetalks_staging import ARCHIVE_PATH, MANIFEST_NAME, materialize_candidate


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def git_text(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def make_repo(root: Path, *, unsafe_link: str | None = None, cycle: bool = False) -> str:
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    origin = "https://github.com/spdk/spdk.git" if root.name == "spdk" else "git@github.com:linux-nvme/nvme-cli.git"
    git(root, "remote", "add", "origin", origin)
    (root / "tracked.c").write_text("tracked\n", encoding="utf-8")
    (root / "target.txt").write_text("target contents\n", encoding="utf-8")
    (root / "stream.json").write_text('{"first": 1}\n{"second": 2}\n', encoding="utf-8")
    (root / "scripts").mkdir()
    executable = root / "scripts" / "autorun_post.py"
    executable.write_text("#!/usr/bin/env python3\nprint('real target')\n", encoding="utf-8")
    executable.chmod(0o755)
    (root / "relative-link").symlink_to("target.txt")
    (root / "autorun_post.py").symlink_to("scripts/autorun_post.py")
    if unsafe_link is not None:
        (root / "unsafe-link").symlink_to(unsafe_link)
    if cycle:
        (root / "cycle-a").symlink_to("cycle-b")
        (root / "cycle-b").symlink_to("cycle-a")
    git(root, "add", ".")

    # A gitlink deliberately references a commit that is not available in the
    # superproject object database.  Staging must still record/materialize it.
    sub = root.parent / f"{root.name}-sub"
    sub.mkdir()
    git(sub, "init")
    git(sub, "config", "user.email", "test@example.invalid")
    git(sub, "config", "user.name", "Test")
    (sub / "sub.txt").write_text("submodule\n", encoding="utf-8")
    git(sub, "add", ".")
    git(sub, "commit", "-m", "sub")
    sub_commit = git_text(sub, "rev-parse", "HEAD")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{sub_commit},vendor/sub")
    git(root, "commit", "-m", "base")
    return git_text(root, "rev-parse", "HEAD")


def manifest_for(spdk: Path, nvme: Path) -> dict:
    return {"repositories": [
        {"id": "spdk", "url": "https://github.com/spdk/spdk", "commit": git_text(spdk, "rev-parse", "HEAD"), "tree": git_text(spdk, "rev-parse", "HEAD^{tree}")},
        {"id": "nvme-cli", "url": "ssh://git@github.com/linux-nvme/nvme-cli.git", "commit": git_text(nvme, "rev-parse", "HEAD"), "tree": git_text(nvme, "rev-parse", "HEAD^{tree}")},
    ]}


def safe_candidate(root: Path) -> Path:
    candidate = root / "candidate"
    (candidate / "core" / "empty").mkdir(parents=True)
    (candidate / "README.md").write_text("safe candidate\n", encoding="utf-8")
    (candidate / "core" / "safe.md").write_text("safe\n", encoding="utf-8")
    return candidate


def _fixture_public_manifest(spdk: Path, nvme: Path) -> dict:
    """Test-only canonical manifest projected onto local mock repository ids."""
    manifest = copy.deepcopy(public_stage.load_manifest())
    commits = {"spdk": git_text(spdk, "rev-parse", "HEAD"),
               "nvme-cli": git_text(nvme, "rev-parse", "HEAD")}
    for case in manifest["cases"]:
        case["frozen_commit"] = commits[case["repository_id"]]
    return manifest


def stage_fixture(
    destination: Path,
    candidate_root: Path,
    spdk: Path,
    nvme: Path,
    *,
    corpus_manifest: dict | None = None,
    case_id: str = "spdk-nvmf-tcp-receive-closure",
    task_prefix: str = "",
    task_suffix: str = "",
    candidate: str = "pangea",
    candidate_manifest_sha256: str | None = None,
) -> dict:
    """Stage through the production API with an explicit test-only authority."""
    public_manifest = _fixture_public_manifest(spdk, nvme)
    case = next(item for item in public_manifest["cases"] if item["id"] == case_id)
    task_text = f"{task_prefix}{case['agent_input']}{task_suffix}"
    with patch("evaluation.corpus.load_corpus_manifest",
               return_value=corpus_manifest or manifest_for(spdk, nvme)), \
            patch("evaluation.corpus.public_stage.load_manifest", return_value=public_manifest), \
            patch("evaluation.corpus.public_stage._validate_manifest_snapshot", return_value=[]):
        return stage_public_corpus(
            destination, candidate_root, task_text, {"spdk": spdk, "nvme-cli": nvme}, case,
            candidate=candidate, candidate_manifest_sha256=candidate_manifest_sha256,
        )


class CorpusStagingTests(unittest.TestCase):
    def _assert_candidate_secret_rejected(self, assignment: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); candidate = root / "candidate"; destination = root / "dest"
            candidate.mkdir(); destination.mkdir()
            payload = destination / "README.md"; payload.write_text(assignment, encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkContractError, "secret marker"):
                _validate_candidate_payload(destination, {"README.md": "unused"}, [candidate])

    def test_anthropic_api_key_assignment_is_rejected(self) -> None:
        self._assert_candidate_secret_rejected("ANTHROPIC_API_KEY=sk-ant-api03-4Y7mA9pQ2vX8cN6kL3sD5fH1\n")

    def test_opencode_api_key_assignment_is_rejected(self) -> None:
        self._assert_candidate_secret_rejected("OPENCODE_API_KEY=ocz_live_7pQ4mN8vR2xK6dF9\n")

    def test_github_token_assignment_is_rejected(self) -> None:
        self._assert_candidate_secret_rejected("GITHUB_TOKEN=github_pat_11ABCD_7pQ4mN8vR2xK6dF9\n")

    def test_gh_token_assignment_is_rejected(self) -> None:
        self._assert_candidate_secret_rejected("GH_TOKEN=ghp_7pQ4mN8vR2xK6dF9sA3c\n")

    def test_opencode_server_password_assignment_is_rejected(self) -> None:
        self._assert_candidate_secret_rejected("OPENCODE_SERVER_PASSWORD=Correct-Horse-Battery-47!\n")

    def test_opencode_server_username_assignment_is_rejected(self) -> None:
        self._assert_candidate_secret_rejected("OPENCODE_SERVER_USERNAME=service-account-pangea-prod\n")

    def test_secret_variable_mentions_and_placeholders_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); candidate = root / "candidate"; destination = root / "dest"
            candidate.mkdir(); destination.mkdir()
            text = """Mention ANTHROPIC_API_KEY and GITHUB_TOKEN without assigning them.
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
OPENCODE_API_KEY=your_opencode_api_key
GITHUB_TOKEN=<github_token>
GH_TOKEN=ghp_...
OPENCODE_SERVER_PASSWORD=changeme
OPENCODE_SERVER_USERNAME=user
"""
            (destination / "README.md").write_text(text, encoding="utf-8")
            _validate_candidate_payload(destination, {"README.md": "unused"}, [candidate])

    def test_provider_secret_policy_is_shared_without_forwarding_scm_tokens(self) -> None:
        self.assertTrue({"DEEPSEEK_API_KEY"} <= ENVIRONMENT_ALLOWLIST)
        self.assertTrue((PROVIDER_CREDENTIAL_ENV_KEYS - {"DEEPSEEK_API_KEY"}).isdisjoint(ENVIRONMENT_ALLOWLIST))
        self.assertTrue(PROVIDER_CREDENTIAL_ENV_KEYS <= CANDIDATE_SECRET_ASSIGNMENT_KEYS)
        self.assertTrue({"GITHUB_TOKEN", "GH_TOKEN"} <= CANDIDATE_SECRET_ASSIGNMENT_KEYS)
        self.assertTrue({"GITHUB_TOKEN", "GH_TOKEN"}.isdisjoint(ENVIRONMENT_ALLOWLIST))

    def test_public_validator_scans_candidate_metadata_but_not_repository_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp); source = bundle / "repositories/spdk/source.txt"
            source.parent.mkdir(parents=True)
            source.write_text("ANTHROPIC_API_KEY=sk-ant-api03-4Y7mA9pQ2vX8cN6kL3sD5fH1\n")
            (bundle / "README.md").write_text('{"GITHUB_TOKEN": "github_pat_11ABCD_7pQ4mN8vR2xK6dF9"}\n')
            write_public_bundle_manifest(bundle)
            errors = validate_public_bundle(bundle)
            self.assertTrue(any("secret assignment exposed: README.md" in error for error in errors), errors)
            self.assertFalse(any("source.txt" in error for error in errors), errors)
            (bundle / "README.md").write_text("GITHUB_TOKEN=<github_token>\n")
            write_public_bundle_manifest(bundle)
            self.assertEqual([], validate_public_bundle(bundle))

    def test_object_staging_real_semantics_readonly_and_source_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spdk, nvme = root / "spdk", root / "nvme"
            make_repo(spdk); make_repo(nvme)
            (spdk / "tracked.c").write_text("dirty\n", encoding="utf-8")
            (spdk / "untracked").write_text("untracked\n", encoding="utf-8")
            candidate = safe_candidate(root)
            before_status = subprocess.run(["git", "-C", str(spdk), "status", "--porcelain=v1", "-z"], check=True, capture_output=True).stdout
            before_index = (spdk / ".git" / "index").read_bytes()
            before_dirty = (spdk / "tracked.c").read_bytes()
            destination = root / "stage"
            receipt = stage_fixture(destination, candidate, spdk, nvme)

            self.assertEqual("tracked\n", (destination / "repositories/spdk/tracked.c").read_text())
            self.assertFalse((destination / "repositories/spdk/untracked").exists())
            self.assertEqual('{"first": 1}\n{"second": 2}\n', (destination / "repositories/spdk/stream.json").read_text())
            self.assertEqual("target contents\n", (destination / "repositories/spdk/relative-link").read_text())
            self.assertIn("real target", (destination / "repositories/spdk/autorun_post.py").read_text())
            self.assertFalse((destination / "repositories/spdk/relative-link").is_symlink())
            self.assertEqual([], validate_public_bundle(destination))

            repo = receipt["repositories"][0]
            self.assertEqual("git-object-v1", repo["materialization_version"])
            self.assertEqual(40, len(repo["git_tree"]))
            self.assertEqual(64, len(repo["materialization_sha256"]))
            self.assertGreaterEqual(repo["entry_counts"]["symlink"], 2)
            self.assertGreaterEqual(repo["entry_counts"]["gitlink"], 1)
            self.assertGreaterEqual(repo["entry_counts"]["executable"], 1)
            self.assertEqual("blob", next(row for row in repo["materialized_symlinks"] if row["path"] == "autorun_post.py")["resolution"])
            self.assertEqual("gitlink", (destination / "repositories/spdk/vendor/sub").read_text().split()[0])
            self.assertTrue((destination / "repositories/spdk/scripts/autorun_post.py").stat().st_mode & 0o111)

            writable = []
            for path in [destination, *destination.rglob("*")]:
                if path.lstat().st_mode & 0o222:
                    writable.append(path.relative_to(destination).as_posix() or ".")
            self.assertEqual(["pangea-data", "pangea-data/.evaluator-scratch"], writable)
            self.assertFalse((destination / ".evaluator-scratch").exists())
            self.assertEqual(before_status, subprocess.run(["git", "-C", str(spdk), "status", "--porcelain=v1", "-z"], check=True, capture_output=True).stdout)
            self.assertEqual(before_index, (spdk / ".git" / "index").read_bytes())
            self.assertEqual(before_dirty, (spdk / "tracked.c").read_bytes())
            public = b"\n".join(path.read_bytes() for path in destination.rglob("*") if path.is_file())
            for forbidden in (candidate.resolve(), spdk.resolve(), nvme.resolve()):
                self.assertNotIn(str(forbidden).encode(), public)

    def test_all_eight_cases_roundtrip_with_identical_pangea_and_fuse_case_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spdk, nvme = root / "spdk", root / "nvme"
            make_repo(spdk); make_repo(nvme)
            pangea_candidate = safe_candidate(root)
            fuse_candidate = root / "fuse-candidate"
            (fuse_candidate / "core").mkdir(parents=True)
            (fuse_candidate / "README.md").write_text("mock Fuse candidate\n", encoding="utf-8")
            fuse_manifest = fuse_candidate / MANIFEST_NAME
            fuse_manifest.write_text('{"candidate":"fuse-fixture"}\n', encoding="utf-8")
            fuse_hash = hashlib.sha256(fuse_manifest.read_bytes()).hexdigest()

            for case_id in (case["id"] for case in public_stage.load_manifest()["cases"]):
                with self.subTest(case=case_id):
                    pangea_root = root / f"pangea-{case_id}"
                    fuse_root = root / f"fuse-{case_id}"
                    pangea = stage_fixture(
                        pangea_root, pangea_candidate, spdk, nvme, case_id=case_id,
                        task_prefix="只读处理并直接开始。\n",
                    )
                    fuse = stage_fixture(
                        fuse_root, fuse_candidate, spdk, nvme, case_id=case_id,
                        task_prefix="只读处理并直接开始。\n", candidate="fuse",
                        candidate_manifest_sha256=fuse_hash,
                    )
                    pangea_case = (pangea_root / "CASE.json").read_bytes()
                    self.assertEqual(pangea_case, (fuse_root / "CASE.json").read_bytes())
                    self.assertEqual(pangea["case_sha256"], fuse["case_sha256"])
                    self.assertEqual(pangea["case_sha256"], hashlib.sha256(pangea_case).hexdigest())
                    self.assertEqual(case_id, pangea["case_id"])
                    self.assertEqual("canonical-agent-input-line-v1", pangea["task_binding_version"])
                    self.assertIsNone(pangea["candidate_manifest_sha256"])
                    self.assertEqual(fuse_hash, fuse["candidate_manifest_sha256"])

    @unittest.skipUnless(ARCHIVE_PATH.is_file() and not ARCHIVE_PATH.is_symlink(), "frozen CodeTalks archive is not available locally")
    def test_fuse_real_materialization_manifest_is_bound_to_public_stage(self) -> None:
        """The evaluator receipt, not candidate self-reporting, binds Fuse staging."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spdk, nvme = root / "spdk", root / "nvme"
            make_repo(spdk); make_repo(nvme)
            candidate = root / "fuse-candidate"
            materialization = materialize_candidate(candidate)
            expected = materialization["manifest_sha256"]
            receipt = stage_fixture(
                root / "stage", candidate, spdk, nvme,
                candidate="fuse", candidate_manifest_sha256=expected,
            )
            self.assertEqual(expected, receipt["candidate_manifest_sha256"])
            self.assertEqual(expected, receipt["candidate_files"][MANIFEST_NAME])
            self.assertEqual(expected, hashlib.sha256((root / "stage" / MANIFEST_NAME).read_bytes()).hexdigest())

            missing = root / "missing-candidate"
            materialize_candidate(missing)
            missing.chmod(0o755)
            (missing / MANIFEST_NAME).chmod(0o644)
            (missing / MANIFEST_NAME).unlink()
            with self.assertRaisesRegex(BenchmarkContractError, "differs"):
                stage_fixture(
                    root / "missing-stage", missing, spdk, nvme,
                    candidate="fuse", candidate_manifest_sha256=expected,
                )

            replaced = root / "replaced-candidate"
            materialize_candidate(replaced)
            replaced.chmod(0o755)
            replacement = replaced / MANIFEST_NAME
            replacement.chmod(0o644)
            replacement.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkContractError, "differs"):
                stage_fixture(
                    root / "replaced-stage", replaced, spdk, nvme,
                    candidate="fuse", candidate_manifest_sha256=expected,
                )

    def test_pangea_stage_does_not_bind_codetalks_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spdk, nvme = root / "spdk", root / "nvme"
            make_repo(spdk); make_repo(nvme)
            candidate = safe_candidate(root)
            receipt = stage_fixture(root / "stage", candidate, spdk, nvme, candidate="pangea")
            self.assertIsNone(receipt["candidate_manifest_sha256"])

    def test_info_attributes_cannot_change_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); spdk, nvme = root / "spdk", root / "nvme"
            make_repo(spdk); make_repo(nvme); candidate = safe_candidate(root)
            manifest = manifest_for(spdk, nvme)
            first = stage_fixture(root / "first", candidate, spdk, nvme, corpus_manifest=manifest)
            (spdk / ".git" / "info" / "attributes").write_text("tracked.c export-ignore\n", encoding="utf-8")
            second = stage_fixture(root / "second", candidate, spdk, nvme, corpus_manifest=manifest)
            self.assertEqual(first, second)
            self.assertTrue((root / "first/repositories/spdk/tracked.c").is_file())
            self.assertTrue((root / "second/repositories/spdk/tracked.c").is_file())
            first_manifest = json.loads((root / "first/public-bundle-manifest.json").read_text())["files"]
            second_manifest = json.loads((root / "second/public-bundle-manifest.json").read_text())["files"]
            self.assertEqual(first_manifest, second_manifest)

    def test_origin_and_frozen_tree_mismatches_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); spdk, nvme = root / "spdk", root / "nvme"
            make_repo(spdk); make_repo(nvme); candidate = safe_candidate(root)
            manifest = manifest_for(spdk, nvme)
            wrong_tree = copy.deepcopy(manifest)
            wrong_tree["repositories"][0]["tree"] = "0" * 40
            with self.assertRaisesRegex(BenchmarkContractError, "resolved tree differs"):
                stage_fixture(root / "tree-dest", candidate, spdk, nvme, corpus_manifest=wrong_tree)
            git(spdk, "remote", "set-url", "origin", "https://github.com/example/not-spdk.git")
            with self.assertRaisesRegex(BenchmarkContractError, "origin does not match"):
                stage_fixture(root / "origin-dest", candidate, spdk, nvme, corpus_manifest=manifest)

    def test_unsafe_and_cyclic_git_symlinks_fail_target_branch(self) -> None:
        for target, cycle, expected in [("../../outside", False, "unsafe git symlink target"), ("/absolute", False, "unsafe git symlink target"), (None, True, "cyclic git symlink")]:
            with self.subTest(target=target, cycle=cycle), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); spdk, nvme = root / "spdk", root / "nvme"
                make_repo(spdk, unsafe_link=target, cycle=cycle); make_repo(nvme)
                with self.assertRaisesRegex(BenchmarkContractError, expected):
                    stage_fixture(root / "dest", safe_candidate(root), spdk, nvme)
                self.assertFalse((root / "dest").exists())

    def test_late_failure_rolls_back_new_and_existing_empty_destinations(self) -> None:
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); spdk, nvme = root / "spdk", root / "nvme"
                make_repo(spdk); make_repo(nvme, unsafe_link="../../escape")
                destination = root / "dest"
                if existing:
                    destination.mkdir()
                with self.assertRaisesRegex(BenchmarkContractError, "unsafe git symlink target"):
                    stage_fixture(destination, safe_candidate(root), spdk, nvme)
                if existing:
                    self.assertTrue(destination.is_dir())
                    self.assertEqual([], list(destination.iterdir()))
                    self.assertTrue(destination.stat().st_mode & 0o200)
                else:
                    self.assertFalse(destination.exists())

    def test_candidate_hardlink_secret_special_and_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = root / "hardlink"; (candidate / "core").mkdir(parents=True)
            secret = root / "outside-secret"; secret.write_text("AWS_ACCESS_KEY_ID=ABCDEFGHIJKLMNOPQRST\n")
            os.link(secret, candidate / "core" / "leak")
            with self.assertRaisesRegex(BenchmarkContractError, "hardlink"):
                _copy_candidate_tree(candidate, root / "hard-dest")

            symlink = root / "symlink"; (symlink / "core").mkdir(parents=True)
            (symlink / "core" / "leak").symlink_to(secret)
            with self.assertRaisesRegex(BenchmarkContractError, "symlink"):
                _copy_candidate_tree(symlink, root / "link-dest")

            fifo = root / "fifo"; fifo.mkdir()
            try:
                os.mkfifo(fifo / "README.md")
            except (AttributeError, OSError):
                self.skipTest("FIFO unsupported")
            with self.assertRaisesRegex(BenchmarkContractError, "special file"):
                _copy_candidate_tree(fifo, root / "fifo-dest")

    def test_candidate_staging_rejects_opencode_plugin_entries_before_filtering(self) -> None:
        variants = {
            "root-config": ("opencode.json", '{"plugin":["./candidate-plugin.mjs"]}'),
            "nested-config": ("core/nested/opencode.jsonc", '{"plugin":["../../../candidate-plugin.mjs"]}'),
            "auto-plugin": (".opencode/plugins/candidate.js", "export default async () => ({})\n"),
            "package-entry": (".opencode/plugin/candidate/package.json", '{"main":"index.mjs"}'),
        }
        for name, (relative, payload) in variants.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                candidate = safe_candidate(root)
                entry = candidate / relative
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text(payload, encoding="utf-8")
                (candidate / "candidate-plugin.mjs").write_text(
                    "export default async () => ({})\n", encoding="utf-8",
                )
                destination = root / "destination"
                destination.mkdir()
                with self.assertRaisesRegex(BenchmarkContractError, "OpenCode project plugin entries"):
                    _copy_candidate_tree(candidate, destination)
                self.assertEqual([], list(destination.iterdir()))

    def test_candidate_cache_build_secret_text_and_absolute_root_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); candidate = root / "candidate"; (candidate / "core/cache").mkdir(parents=True)
            (candidate / "core/build").mkdir()
            (candidate / "core/empty").mkdir()
            (candidate / "core/cache/leak").write_text("secret")
            (candidate / "core/build/generated").write_text("generated")
            (candidate / "core/safe").write_text("safe")
            destination = root / "dest"; destination.mkdir()
            files, directories, _ = _copy_candidate_tree(candidate, destination)
            self.assertNotIn("core/cache/leak", files)
            self.assertNotIn("core/build/generated", files)
            self.assertNotIn("core/cache", directories)
            self.assertIn("core/empty", directories)
            (destination / "core/safe").write_text("AWS_ACCESS_KEY_ID=ABCDEFGHIJKLMNOPQRST\n")
            with self.assertRaisesRegex(BenchmarkContractError, "secret marker"):
                _validate_candidate_payload(destination, files, [candidate])
            (destination / "core/safe").write_text(str(candidate.resolve()), encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkContractError, "absolute source root"):
                _validate_candidate_payload(destination, files, [candidate])

    def test_candidate_tree_digest_is_path_independent_and_binds_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hashes = []
            for name in ("one", "two"):
                source = root / name; (source / "core/empty").mkdir(parents=True)
                (source / "core/file").write_text("same")
                destination = root / f"{name}-dest"; destination.mkdir()
                hashes.append(_copy_candidate_tree(source, destination)[2])
            self.assertEqual(hashes[0], hashes[1])
            (root / "two/core/empty").rmdir()
            third = root / "third-dest"; third.mkdir()
            self.assertNotEqual(hashes[0], _copy_candidate_tree(root / "two", third)[2])

    def test_metadata_invalid_json_rejected_but_repository_json_stream_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / "repositories/spdk").mkdir(parents=True)
            (bundle / "repositories/spdk/stream.json").write_text('{"a": 1}\n{"b": 2}\n')
            (bundle / "stage-receipt.json").write_text('{"broken":')
            write_public_bundle_manifest(bundle)
            errors = validate_public_bundle(bundle)
            self.assertTrue(any("cannot inspect JSON stage-receipt.json" in error for error in errors), errors)
            (bundle / "stage-receipt.json").write_text('{"safe": true}\n')
            write_public_bundle_manifest(bundle)
            self.assertEqual([], validate_public_bundle(bundle))
            (bundle / "unmanifested-empty").mkdir()
            self.assertTrue(any("visible directories" in error for error in validate_public_bundle(bundle)))
            (bundle / "unmanifested-empty").rmdir()
            (bundle / "stage-receipt.json").write_text('{"scoring": []}\n')
            write_public_bundle_manifest(bundle)
            self.assertTrue(any("private oracle field exposed: stage-receipt.json" in error for error in validate_public_bundle(bundle)))

    def test_manifest_contract_rejects_duplicates_mounts_and_extra_fields(self) -> None:
        base = json.loads(Path("benchmarks/evaluation/corpus-manifest.json").read_text())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "manifest.json"
            compatible = copy.deepcopy(base)
            compatible["repositories"][0]["url"] = "git@github.com:spdk/spdk.git"
            path.write_text(json.dumps(compatible))
            self.assertEqual("spdk", load_corpus_manifest(path)["repositories"][0]["id"])
            variants = []
            swapped = copy.deepcopy(base)
            swapped["repositories"][0]["mount"], swapped["repositories"][1]["mount"] = swapped["repositories"][1]["mount"], swapped["repositories"][0]["mount"]
            variants.append(swapped)
            duplicate = copy.deepcopy(base); duplicate["repositories"][1]["id"] = "spdk"; variants.append(duplicate)
            extra = copy.deepcopy(base); extra["repositories"][0]["extra"] = True; variants.append(extra)
            top_extra = copy.deepcopy(base); top_extra["extra"] = True; variants.append(top_extra)
            for index, value in enumerate(variants):
                with self.subTest(index=index):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(BenchmarkContractError):
                        load_corpus_manifest(path)


if __name__ == "__main__":
    unittest.main()
