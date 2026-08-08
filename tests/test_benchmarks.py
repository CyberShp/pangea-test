from __future__ import annotations

import copy
import ctypes
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from benchmarks import stage


EXPECTED_FIELDS = {
    "id", "title", "repository_id", "repository_url", "frozen_commit", "mode",
    "phase_membership", "source_scope", "contract", "agent_input", "safety_boundary",
}
EXPECTED_CASES = {
    "spdk-nvmf-tcp-receive-closure": ("spdk", "https://github.com/spdk/spdk", "97af299e3c76368219f0cddcc710fafd57edcc1c", {"pilot", "full"}),
    "spdk-nvme-rdma-reset-recovery": ("spdk", "https://github.com/spdk/spdk", "97af299e3c76368219f0cddcc710fafd57edcc1c", {"full"}),
    "spdk-nvmf-tcp-resource-recovery": ("spdk", "https://github.com/spdk/spdk", "97af299e3c76368219f0cddcc710fafd57edcc1c", {"full"}),
    "spdk-recv-state-diagnostics": ("spdk", "https://github.com/spdk/spdk", "97af299e3c76368219f0cddcc710fafd57edcc1c", {"smoke", "pilot", "full"}),
    "nvme-cli-command-dispatch": ("nvme-cli", "https://github.com/linux-nvme/nvme-cli", "cc00f4fd5d8262c440d033de9504ebf641880e62", {"smoke", "pilot", "full"}),
    "nvme-cli-format-safety": ("nvme-cli", "https://github.com/linux-nvme/nvme-cli", "cc00f4fd5d8262c440d033de9504ebf641880e62", {"pilot", "full"}),
    "nvme-cli-sanitize-status": ("nvme-cli", "https://github.com/linux-nvme/nvme-cli", "cc00f4fd5d8262c440d033de9504ebf641880e62", {"full"}),
    "nvme-cli-parse-open-boundary": ("nvme-cli", "https://github.com/linux-nvme/nvme-cli", "cc00f4fd5d8262c440d033de9504ebf641880e62", {"full"}),
}
EXPECTED_TARGETS = {
    "spdk": (Path("/Volumes/Media/dpdk/spdk"), "https://github.com/spdk/spdk", "97af299e3c76368219f0cddcc710fafd57edcc1c", "3718a94e7956cd5f15a1e8edb65d6bbeacef9c7d"),
    "nvme-cli": (Path("/Volumes/Media/nvme-cli"), "https://github.com/linux-nvme/nvme-cli", "cc00f4fd5d8262c440d033de9504ebf641880e62", "a0f34ca372b1fe44cba2bfd1be1a02c2ba808349"),
}
EXPECTED_SCOPE_PATHS = {
    "spdk-nvmf-tcp-receive-closure": (
        "include/spdk_internal/nvme_tcp.h", "lib/nvmf/tcp.c",
        "test/unit/lib/nvmf/tcp.c/tcp_ut.c",
    ),
    "spdk-nvme-rdma-reset-recovery": (
        "lib/nvme/nvme_ctrlr.c", "lib/nvme/nvme_internal.h", "lib/nvme/nvme_qpair.c",
        "lib/nvme/nvme_rdma.c", "test/unit/lib/nvme/nvme_ctrlr.c/nvme_ctrlr_ut.c",
        "test/unit/lib/nvme/nvme_qpair.c/nvme_qpair_ut.c",
        "test/unit/lib/nvme/nvme_rdma.c/nvme_rdma_ut.c",
    ),
    "spdk-nvmf-tcp-resource-recovery": (
        "include/spdk_internal/nvme_tcp.h", "lib/nvmf/tcp.c",
        "test/unit/lib/nvmf/tcp.c/tcp_ut.c",
    ),
    "spdk-recv-state-diagnostics": (
        "include/spdk_internal/nvme_tcp.h", "lib/nvme/nvme_tcp.c",
        "test/unit/lib/nvme/nvme_tcp.c/nvme_tcp_ut.c",
    ),
    "nvme-cli-command-dispatch": ("cmd_handler.h", "nvme-builtin.h", "nvme.c", "plugin.c"),
    "nvme-cli-format-safety": (
        "cmd_handler.h", "libnvme/src/nvme/ioctl.c", "libnvme/src/nvme/lib.c",
        "nvme-builtin.h", "nvme.c", "nvme.h", "plugin.c",
    ),
    "nvme-cli-sanitize-status": (
        "libnvme/src/nvme/ioctl.c", "nvme-builtin.h", "nvme.c", "nvme.h",
    ),
    "nvme-cli-parse-open-boundary": ("libnvme/src/nvme/lib.c", "nvme.c", "nvme.h"),
}


class BenchmarkManifestTests(unittest.TestCase):
    def _write_manifest(self, directory: Path, payload: object) -> Path:
        (directory / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return directory

    def _schema(self) -> Draft202012Validator:
        schema = json.loads(Path("benchmarks/evaluation/public-case.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def test_exact_frozen_cases_and_phase_matrix_using_independent_literals(self) -> None:
        self.assertEqual([], stage.validate_manifest())
        cases = stage.load_manifest()["cases"]
        self.assertEqual(set(EXPECTED_CASES), {case["id"] for case in cases})
        for case in cases:
            expected_repo, expected_url, expected_commit, expected_phases = EXPECTED_CASES[case["id"]]
            self.assertEqual(EXPECTED_FIELDS, set(case))
            self.assertEqual((expected_repo, expected_url, expected_commit, expected_phases),
                             (case["repository_id"], case["repository_url"], case["frozen_commit"],
                              set(case["phase_membership"])))
            self.assertEqual(EXPECTED_SCOPE_PATHS[case["id"]], tuple(case["source_scope"]["paths"]))
        self.assertEqual(34, sum(len(paths) for paths in EXPECTED_SCOPE_PATHS.values()))

    def test_each_scope_is_frozen_by_schema_and_python_against_add_or_remove(self) -> None:
        base = stage.load_manifest()
        validator = self._schema()
        for case_index, original in enumerate(base["cases"]):
            mutations = (
                ("remove", lambda paths: paths.pop()),
                ("add", lambda paths: paths.append("README.md")),
                ("reorder", lambda paths: paths.reverse()),
            )
            for mutation_name, mutate in mutations:
                with self.subTest(case=original["id"], mutation=mutation_name), \
                        tempfile.TemporaryDirectory() as temp:
                    changed = copy.deepcopy(base)
                    mutate(changed["cases"][case_index]["source_scope"]["paths"])
                    self.assertTrue(list(validator.iter_errors(changed["cases"][case_index])))
                    errors = stage.validate_manifest(self._write_manifest(Path(temp), changed))
                    self.assertTrue(any("source_scope paths do not match frozen case scope" in item
                                        for item in errors), errors)

    def test_frozen_config_and_git_commit_trees_match_independent_literals(self) -> None:
        frozen = json.loads(Path("benchmarks/evaluation/frozen-config.json").read_text(encoding="utf-8"))
        actual_targets = {item["id"]: (item["repository"], item["commit"], item["tree"])
                          for item in frozen["targets"]}
        for repo_id, (repo, url, commit, tree) in EXPECTED_TARGETS.items():
            self.assertTrue(repo.is_dir(), f"required frozen repository missing: {repo}")
            self.assertEqual((url, commit, tree), actual_targets[repo_id])
            result = subprocess.run(["git", "-C", str(repo), "rev-parse", f"{commit}^{{tree}}"],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(tree, result.stdout.strip())

    def test_every_scope_path_is_a_blob_at_the_frozen_commit(self) -> None:
        for case in stage.load_manifest()["cases"]:
            repo, _url, commit, _tree = EXPECTED_TARGETS[case["repository_id"]]
            self.assertTrue(repo.is_dir(), f"required frozen repository missing: {repo}")
            for path in case["source_scope"]["paths"]:
                with self.subTest(case=case["id"], path=path):
                    result = subprocess.run(["git", "-C", str(repo), "cat-file", "-t", f"{commit}:{path}"],
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual("blob", result.stdout.strip())

    def test_schema_is_executed_and_accepts_every_public_case(self) -> None:
        validator = self._schema()
        for case in stage.load_manifest()["cases"]:
            self.assertEqual([], list(validator.iter_errors(case)), case["id"])

    def test_schema_and_python_both_reject_adversarial_case_values(self) -> None:
        base = stage.load_manifest()
        mutations = [
            ("blank-title", lambda case: case.__setitem__("title", " \t")),
            ("backslash-path", lambda case: case["source_scope"].__setitem__("paths", ["lib\\nvme.c"])),
            ("double-slash", lambda case: case["source_scope"].__setitem__("paths", ["lib//nvme.c"])),
            ("trailing-slash", lambda case: case["source_scope"].__setitem__("paths", ["lib/nvme.c/"])),
            ("nul-path", lambda case: case["source_scope"].__setitem__("paths", ["lib/nvme\x00.c"])),
            ("dot-path", lambda case: case["source_scope"].__setitem__("paths", ["./nvme.c"])),
            ("whitespace-path", lambda case: case["source_scope"].__setitem__("paths", [" nvme.c"])),
            ("empty-hint", lambda case: case["source_scope"].__setitem__("symbol_hints", [" "])),
            ("wrong-url", lambda case: case.__setitem__("repository_url", "https://github.com/linux-nvme/nvme-cli")),
            ("wrong-commit", lambda case: case.__setitem__("frozen_commit", "cc00f4fd5d8262c440d033de9504ebf641880e62")),
            ("paired-wrong-repo", lambda case: (case.__setitem__("repository_id", "nvme-cli"), case.__setitem__("repository_url", "https://github.com/linux-nvme/nvme-cli"), case.__setitem__("frozen_commit", "cc00f4fd5d8262c440d033de9504ebf641880e62"))),
            ("malformed-id", lambda case: case.__setitem__("id", ["not", "a", "string"])),
            ("unknown-id", lambda case: case.__setitem__("id", "syntactically-valid-but-unknown")),
            ("malformed-phase", lambda case: case.__setitem__("phase_membership", {"full": True})),
            ("wrong-valid-phase", lambda case: case.__setitem__("phase_membership", ["full"])),
        ]
        validator = self._schema()
        for name, mutate in mutations:
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as temp:
                mutated = copy.deepcopy(base)
                mutate(mutated["cases"][0])
                self.assertTrue(list(validator.iter_errors(mutated["cases"][0])))
                root = self._write_manifest(Path(temp), mutated)
                self.assertTrue(stage.validate_manifest(root))

    def test_malformed_top_level_and_duplicate_id_return_errors_without_raising(self) -> None:
        payloads: list[object] = [[], "bad", None, {"schema_version": "2.0", "cases": {}}]
        duplicate = copy.deepcopy(stage.load_manifest())
        duplicate["cases"][1]["id"] = duplicate["cases"][0]["id"]
        payloads.append(duplicate)
        for payload in payloads:
            with self.subTest(payload_type=type(payload).__name__), tempfile.TemporaryDirectory() as temp:
                self.assertTrue(stage.validate_manifest(self._write_manifest(Path(temp), payload)))

    def test_public_cases_exclude_private_markers_and_destructive_cases_forbid_real_devices(self) -> None:
        cases = stage.load_manifest()["cases"]
        public_text = json.dumps(cases, ensure_ascii=False).lower()
        for marker in ("fault_mode", "evidence_keywords", "scoring", "sealed_oracle", "oracle_answer",
                       "expected findings", "expected_findings", "skill triggers", "skill_triggers", "mutations"):
            self.assertNotIn(marker, public_text)
        by_id = {case["id"]: case for case in cases}
        for case_id in {"nvme-cli-format-safety", "nvme-cli-sanitize-status"}:
            self.assertIn("禁止真实设备", by_id[case_id]["safety_boundary"])

    def test_schema_and_python_reject_the_same_private_marker_vocabulary(self) -> None:
        validator = self._schema()
        markers = (
            "fault_mode", "evidence_keywords", "scoring", "oracle",
            "expected findings", "expected_findings", "skill triggers",
            "skill_triggers", "mutations", "oracle answer", "OrAcLe AnSwEr",
        )
        self.assertEqual(set(markers[:9]), stage.FORBIDDEN_MARKERS)
        for marker in markers:
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as temp:
                manifest = copy.deepcopy(stage.load_manifest())
                manifest["cases"][0]["agent_input"] += f" {marker}"
                self.assertTrue(list(validator.iter_errors(manifest["cases"][0])))
                self.assertTrue(stage._contains_forbidden(manifest["cases"][0]))
                self.assertTrue(stage.validate_manifest(
                    self._write_manifest(Path(temp), manifest)))

    def test_schema_patterns_compile_as_ecmascript_in_node(self) -> None:
        schema_path = Path("benchmarks/evaluation/public-case.schema.json").resolve()
        script = r"""
const fs = require('fs');
const schema = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
function walk(value) {
  if (Array.isArray(value)) return value.forEach(walk);
  if (value && typeof value === 'object') {
    if (typeof value.pattern === 'string') new RegExp(value.pattern);
    Object.values(value).forEach(walk);
  }
}
walk(schema);
"""
        result = subprocess.run(["node", "-e", script, str(schema_path)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_top_level_description_is_explicitly_not_staged(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as manifest_temp, tempfile.TemporaryDirectory() as output_temp:
            manifest = copy.deepcopy(stage.load_manifest())
            marker_text = "description-only oracle scoring marker"
            manifest["description"] = marker_text
            root = self._write_manifest(Path(manifest_temp), manifest)
            self.assertEqual([], stage.validate_manifest(root))
            trusted = Path(output_temp).resolve()
            destination = stage.stage_case(case_id, trusted / "case", root=root,
                                           staging_root=trusted)
            staged = "\n".join(path.read_text(encoding="utf-8")
                               for path in destination.iterdir())
            self.assertNotIn(marker_text, staged)

    def test_staging_writes_exact_public_files_below_explicit_trusted_root(self) -> None:
        for case in stage.load_manifest()["cases"]:
            with self.subTest(case=case["id"]), tempfile.TemporaryDirectory() as temp:
                trusted = Path(temp).resolve()
                destination = stage.stage_case(case["id"], trusted / "public", staging_root=trusted)
                self.assertEqual({"TASK.md", "CASE.json"}, {path.name for path in destination.iterdir()})
                self.assertEqual(case, json.loads((destination / "CASE.json").read_text(encoding="utf-8")))
                staged_text = "\n".join(path.read_text(encoding="utf-8") for path in destination.iterdir()).lower()
                self.assertNotIn("oracle", staged_text)
                self.assertNotIn("scoring", staged_text)
                self.assertEqual(0o555, stat.S_IMODE(destination.stat().st_mode))
                self.assertEqual({0o444}, {stat.S_IMODE(path.stat().st_mode)
                                          for path in destination.iterdir()})

    def test_nonempty_destination_is_preserved_and_rejected(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            destination = trusted / "occupied"
            destination.mkdir()
            sentinel = destination / ".case-existing.tmp"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(stage.BenchmarkError):
                stage.stage_case(case_id, destination, staging_root=trusted)
            self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_traversal_escape_and_nested_symlink_ancestors_fail_closed(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            temporary_root = Path(temp).resolve()
            trusted = temporary_root / "trusted"
            outside = temporary_root / "outside"
            trusted.mkdir()
            outside.mkdir()
            with self.assertRaises(stage.BenchmarkError):
                stage.stage_case(case_id, trusted / ".." / "outside" / "case", staging_root=trusted)
            with self.assertRaises(stage.BenchmarkError):
                stage.stage_case(case_id, outside / "case", staging_root=trusted)
            (trusted / "nested").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(stage.BenchmarkError):
                stage.stage_case(case_id, trusted / "nested" / "case", staging_root=trusted)
            with self.assertRaises(stage.BenchmarkError):
                stage.stage_case(case_id, trusted / "missing-ancestor" / "case", staging_root=trusted)
            self.assertEqual([], list(outside.iterdir()))

    def test_symlink_in_trusted_root_ancestry_fails_closed(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            temporary_root = Path(temp).resolve()
            real = temporary_root / "real"
            real.mkdir()
            link = temporary_root / "linked-root"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(stage.BenchmarkError):
                stage.stage_case(case_id, link / "case", staging_root=link)
            self.assertEqual([], list(real.iterdir()))

    def test_competing_regular_file_at_final_name_is_preserved(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            destination = trusted / "case"
            real_publish = stage._publish_directory_noreplace

            def publish_competitor(parent_fd: int, source: str, final: str) -> None:
                fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o600, dir_fd=parent_fd)
                try:
                    os.write(fd, b"concurrent final\n")
                finally:
                    os.close(fd)
                real_publish(parent_fd, source, final)

            with mock.patch("benchmarks.stage._publish_directory_noreplace",
                            side_effect=publish_competitor):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, destination, staging_root=trusted)
            self.assertEqual("concurrent final\n", destination.read_text(encoding="utf-8"))
            private = [item for item in trusted.iterdir() if item.name.startswith(".pangea-")]
            self.assertEqual(1, len(private), "publish-attempt residue must remain auditable")

    def test_competing_symlink_at_final_name_is_preserved(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            destination = trusted / "case"
            target = trusted / "sentinel"
            target.write_text("preserve", encoding="utf-8")
            real_publish = stage._publish_directory_noreplace

            def publish_symlink(parent_fd: int, source: str, final: str) -> None:
                os.symlink("sentinel", final, dir_fd=parent_fd)
                real_publish(parent_fd, source, final)

            with mock.patch("benchmarks.stage._publish_directory_noreplace",
                            side_effect=publish_symlink):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, destination, staging_root=trusted)
            self.assertTrue(destination.is_symlink())
            self.assertEqual("preserve", target.read_text(encoding="utf-8"))

    def test_private_member_regular_replacement_is_detected_and_not_published(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            real_write = stage._write_bundle_file

            def replace_member(dir_fd: int, name: str, payload: bytes):
                result = real_write(dir_fd, name, payload)
                if name == "CASE.json":
                    os.unlink(name, dir_fd=dir_fd)
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o600, dir_fd=dir_fd)
                    os.write(fd, b"replacement\n")
                    os.close(fd)
                return result

            with mock.patch("benchmarks.stage._write_bundle_file", side_effect=replace_member):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertFalse((trusted / "case").exists())
            private = list(trusted.iterdir())
            self.assertEqual(1, len(private))
            self.assertEqual("replacement\n", (private[0] / "CASE.json").read_text(encoding="utf-8"))

    def test_private_member_symlink_replacement_is_detected_and_not_published(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            real_write = stage._write_bundle_file

            def replace_member(dir_fd: int, name: str, payload: bytes):
                result = real_write(dir_fd, name, payload)
                if name == "CASE.json":
                    os.unlink(name, dir_fd=dir_fd)
                    os.symlink("TASK.md", name, dir_fd=dir_fd)
                return result

            with mock.patch("benchmarks.stage._write_bundle_file", side_effect=replace_member):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertFalse((trusted / "case").exists())
            private = list(trusted.iterdir())
            self.assertEqual(1, len(private))
            self.assertTrue((private[0] / "CASE.json").is_symlink())

    def test_same_inode_content_mutation_after_publish_is_detected(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            real_publish = stage._publish_directory_noreplace

            def mutate_after_publish(parent_fd: int, source: str, final: str) -> None:
                real_publish(parent_fd, source, final)
                case_dir = os.open(final, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent_fd)
                try:
                    os.chmod("CASE.json", 0o600, dir_fd=case_dir)
                    fd = os.open("CASE.json", os.O_WRONLY | os.O_TRUNC, dir_fd=case_dir)
                    os.write(fd, b"mutated same inode\n")
                    os.close(fd)
                finally:
                    os.close(case_dir)

            with mock.patch("benchmarks.stage._publish_directory_noreplace",
                            side_effect=mutate_after_publish):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertTrue((trusted / "case").is_dir())

    def test_extra_member_after_publish_is_detected(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            real_publish = stage._publish_directory_noreplace

            def add_after_publish(parent_fd: int, source: str, final: str) -> None:
                real_publish(parent_fd, source, final)
                case_dir = os.open(final, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent_fd)
                try:
                    fd = os.open("EXTRA", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o600, dir_fd=case_dir)
                    os.close(fd)
                finally:
                    os.close(case_dir)

            with mock.patch("benchmarks.stage._publish_directory_noreplace",
                            side_effect=add_after_publish):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertTrue((trusted / "case" / "EXTRA").exists())

    def test_directory_replacement_during_publish_is_not_deleted(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            real_publish = stage._publish_directory_noreplace

            def replace_directory(parent_fd: int, source: str, final: str) -> None:
                os.rename(source, ".stolen", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.mkdir(source, dir_fd=parent_fd)
                real_publish(parent_fd, source, final)

            with mock.patch("benchmarks.stage._publish_directory_noreplace",
                            side_effect=replace_directory):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertTrue((trusted / "case").is_dir())
            self.assertEqual([], list((trusted / "case").iterdir()))
            self.assertTrue((trusted / ".stolen" / "CASE.json").is_file())

    def test_ancestor_rename_before_publish_is_detected_by_root_rewalk(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            outer = Path(temp).resolve()
            trusted = outer / "trusted"
            nested = trusted / "nested"
            nested.mkdir(parents=True)
            moved = trusted / "moved"
            real_write = stage._write_bundle_file
            changed = False

            def rename_ancestor(dir_fd: int, name: str, payload: bytes):
                nonlocal changed
                result = real_write(dir_fd, name, payload)
                if not changed:
                    changed = True
                    nested.rename(moved)
                    nested.mkdir()
                return result

            with mock.patch("benchmarks.stage._write_bundle_file", side_effect=rename_ancestor):
                with self.assertRaisesRegex(stage.BenchmarkError, "ancestor|parent"):
                    stage.stage_case(case_id, nested / "case", staging_root=trusted)
            self.assertFalse((nested / "case").exists())
            self.assertTrue(any(item.name.startswith(".pangea-") for item in moved.iterdir()))

    def test_trusted_root_replacement_after_publish_is_detected(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            outer = Path(temp).resolve()
            trusted = outer / "trusted"
            trusted.mkdir()
            moved = outer / "trusted-original"
            real_fsync = os.fsync
            directory_fsyncs = 0

            def replace_after_parent_fsync(fd: int) -> None:
                nonlocal directory_fsyncs
                real_fsync(fd)
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_fsyncs += 1
                    if directory_fsyncs == 3:
                        trusted.rename(moved)
                        trusted.mkdir()

            with mock.patch("benchmarks.stage.os.fsync", side_effect=replace_after_parent_fsync):
                with self.assertRaisesRegex(stage.BenchmarkError, "ancestor|path|unavailable"):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertFalse((trusted / "case").exists())
            self.assertTrue((moved / "case").is_dir())

    def test_post_parent_fsync_final_snapshot_rejects_all_visible_mutations(self) -> None:
        case_id = next(iter(EXPECTED_CASES))

        def make_writable_directory(destination: Path) -> int:
            fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
            os.fchmod(fd, 0o755)
            return fd

        def replace_member(destination: Path, *, symlink: bool = False) -> None:
            fd = make_writable_directory(destination)
            try:
                os.unlink("CASE.json", dir_fd=fd)
                if symlink:
                    os.symlink("TASK.md", "CASE.json", dir_fd=fd)
                else:
                    member = os.open("CASE.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                     0o444, dir_fd=fd)
                    os.write(member, b"replacement\n")
                    os.close(member)
                os.fchmod(fd, 0o555)
            finally:
                os.close(fd)

        def mutate_same_inode(destination: Path) -> None:
            path = destination / "CASE.json"
            path.chmod(0o600)
            with path.open("wb") as output:
                output.write(b"same inode mutation\n")
            path.chmod(0o444)

        def add_extra(destination: Path) -> None:
            fd = make_writable_directory(destination)
            try:
                member = os.open("EXTRA", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o444, dir_fd=fd)
                os.close(member)
                os.fchmod(fd, 0o555)
            finally:
                os.close(fd)

        def change_file_mode(destination: Path) -> None:
            (destination / "CASE.json").chmod(0o644)

        def change_directory_mode(destination: Path) -> None:
            destination.chmod(0o755)

        def replace_destination(destination: Path, *, symlink: bool = False) -> None:
            retained = destination.with_name("retained-original")
            destination.rename(retained)
            if symlink:
                destination.symlink_to("retained-original", target_is_directory=True)
            else:
                destination.write_text("competing regular file\n", encoding="utf-8")

        mutations = {
            "member-regular": lambda path: replace_member(path),
            "member-symlink": lambda path: replace_member(path, symlink=True),
            "same-inode-content": mutate_same_inode,
            "extra-entry": add_extra,
            "file-mode": change_file_mode,
            "directory-mode": change_directory_mode,
            "destination-regular": lambda path: replace_destination(path),
            "destination-symlink": lambda path: replace_destination(path, symlink=True),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as temp:
                trusted = Path(temp).resolve()
                destination = trusted / "case"
                real_fsync = os.fsync
                directory_fsyncs = 0

                def mutate_after_parent_fsync(fd: int) -> None:
                    nonlocal directory_fsyncs
                    real_fsync(fd)
                    if stat.S_ISDIR(os.fstat(fd).st_mode):
                        directory_fsyncs += 1
                        if directory_fsyncs == 3:
                            mutate(destination)

                with mock.patch("benchmarks.stage.os.fsync", side_effect=mutate_after_parent_fsync):
                    with self.assertRaises(stage.BenchmarkError):
                        stage.stage_case(case_id, destination, staging_root=trusted)

    def test_two_epoch_protocol_rejects_temporary_extra_add_remove_aba(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            destination = trusted / "case"
            real_verify = stage._verify_published_path
            verify_calls = 0

            def verify_with_aba(*args, **kwargs) -> None:
                nonlocal verify_calls
                verify_calls += 1
                if verify_calls != 2:
                    return real_verify(*args, **kwargs)
                real_listdir = os.listdir
                destination_lists = 0

                def add_then_remove(fd: int):
                    nonlocal destination_lists
                    observed = real_listdir(fd)
                    if {"CASE.json", "TASK.md"} <= set(observed):
                        destination_lists += 1
                        os.fchmod(fd, 0o755)
                        if destination_lists == 1:
                            extra = os.open("EXTRA", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                            0o444, dir_fd=fd)
                            os.close(extra)
                            os.fchmod(fd, 0o555)
                            return observed
                        if destination_lists == 2:
                            os.unlink("EXTRA", dir_fd=fd)
                            os.fchmod(fd, 0o555)
                            return real_listdir(fd)
                        os.fchmod(fd, 0o555)
                    return observed

                with mock.patch("benchmarks.stage.os.listdir", side_effect=add_then_remove):
                    return real_verify(*args, **kwargs)

            with mock.patch("benchmarks.stage._verify_published_path", side_effect=verify_with_aba):
                with self.assertRaisesRegex(stage.BenchmarkError, "epoch|changed"):
                    stage.stage_case(case_id, destination, staging_root=trusted)

    def test_two_epoch_protocol_rejects_torn_files_with_no_common_valid_time(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            destination = trusted / "case"
            manifest_case = next(case for case in stage.load_manifest()["cases"]
                                 if case["id"] == case_id)
            expected = {
                "CASE.json": (json.dumps(manifest_case, ensure_ascii=False, indent=2) + "\n").encode(),
                "TASK.md": (manifest_case["agent_input"] + "\n").encode(),
            }
            real_verify = stage._verify_published_path
            verify_calls = 0

            def overwrite(path: Path, payload: bytes) -> None:
                path.chmod(0o600)
                path.write_bytes(payload)
                path.chmod(0o444)

            def verify_torn_files(*args, **kwargs) -> None:
                nonlocal verify_calls
                verify_calls += 1
                if verify_calls != 2:
                    return real_verify(*args, **kwargs)
                case_path = destination / "CASE.json"
                task_path = destination / "TASK.md"
                overwrite(task_path, b"invalid task\n")
                real_read = stage._read_fd
                reads = 0

                def alternate_valid_file(fd: int) -> bytes:
                    nonlocal reads
                    reads += 1
                    if reads == 2:
                        overwrite(case_path, b"invalid case\n")
                        overwrite(task_path, expected["TASK.md"])
                    elif reads == 3:
                        overwrite(task_path, b"invalid task again\n")
                        overwrite(case_path, expected["CASE.json"])
                    elif reads == 4:
                        overwrite(case_path, b"invalid case again\n")
                        overwrite(task_path, expected["TASK.md"])
                    return real_read(fd)

                with mock.patch("benchmarks.stage._read_fd", side_effect=alternate_valid_file):
                    return real_verify(*args, **kwargs)

            with mock.patch("benchmarks.stage._verify_published_path",
                            side_effect=verify_torn_files):
                with self.assertRaisesRegex(stage.BenchmarkError, "epoch|changed"):
                    stage.stage_case(case_id, destination, staging_root=trusted)

    def test_two_epoch_protocol_rejects_content_corrupt_then_restore_aba(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            destination = trusted / "case"
            real_verify = stage._verify_published_path
            real_capture = stage._capture_published_epoch
            verify_calls = 0
            captures = 0

            def verify_with_capture_hook(*args, **kwargs) -> None:
                nonlocal verify_calls, captures
                verify_calls += 1
                if verify_calls != 2:
                    return real_verify(*args, **kwargs)

                def corrupt_restore(*capture_args, **capture_kwargs):
                    nonlocal captures
                    result = real_capture(*capture_args, **capture_kwargs)
                    captures += 1
                    if captures == 1:
                        member = destination / "CASE.json"
                        original = member.read_bytes()
                        member.chmod(0o600)
                        member.write_bytes(b"temporary corruption\n")
                        member.write_bytes(original)
                        member.chmod(0o444)
                    return result

                with mock.patch("benchmarks.stage._capture_published_epoch",
                                side_effect=corrupt_restore):
                    return real_verify(*args, **kwargs)

            with mock.patch("benchmarks.stage._verify_published_path",
                            side_effect=verify_with_capture_hook):
                with self.assertRaisesRegex(stage.BenchmarkError, "epoch|changed"):
                    stage.stage_case(case_id, destination, staging_root=trusted)

    def test_two_epoch_protocol_rejects_ancestor_rename_restore_aba(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            outer = Path(temp).resolve()
            trusted = outer / "trusted"
            trusted.mkdir()
            destination = trusted / "case"
            real_verify = stage._verify_published_path
            real_capture_chain = stage._capture_directory_chain
            verify_calls = 0
            chain_captures = 0

            def verify_with_ancestor_aba(*args, **kwargs) -> None:
                nonlocal verify_calls, chain_captures
                verify_calls += 1
                if verify_calls != 2:
                    return real_verify(*args, **kwargs)

                def rename_restore(*chain_args, **chain_kwargs):
                    nonlocal chain_captures
                    result = real_capture_chain(*chain_args, **chain_kwargs)
                    chain_captures += 1
                    if chain_captures == 1:
                        moved = outer / "trusted-moved"
                        trusted.rename(moved)
                        moved.rename(trusted)
                    return result

                with mock.patch("benchmarks.stage._capture_directory_chain",
                                side_effect=rename_restore):
                    return real_verify(*args, **kwargs)

            with mock.patch("benchmarks.stage._verify_published_path",
                            side_effect=verify_with_ancestor_aba):
                with self.assertRaisesRegex(stage.BenchmarkError, "epoch|changed"):
                    stage.stage_case(case_id, destination, staging_root=trusted)

    def test_epoch_protocol_fails_closed_without_observable_timestamp_advance(self) -> None:
        with tempfile.NamedTemporaryFile() as temporary:
            info = os.fstat(temporary.fileno())
            with self.assertRaisesRegex(stage.BenchmarkError, "epoch change"):
                stage._require_epoch_advance(info, info, "injected coarse timestamp")

    def test_same_uid_mutation_after_final_snapshot_is_explicitly_outside_boundary(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        def writable_dir(destination: Path) -> int:
            fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
            os.fchmod(fd, 0o755)
            return fd

        def replace_member(destination: Path, symlink: bool) -> None:
            fd = writable_dir(destination)
            os.unlink("CASE.json", dir_fd=fd)
            if symlink:
                os.symlink("TASK.md", "CASE.json", dir_fd=fd)
            else:
                member = os.open("CASE.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                 0o444, dir_fd=fd)
                os.close(member)
            os.fchmod(fd, 0o555)
            os.close(fd)

        def mutate_content(destination: Path) -> None:
            member = destination / "CASE.json"
            member.chmod(0o600)
            member.write_text("post-snapshot mutation\n", encoding="utf-8")
            member.chmod(0o444)

        def add_extra(destination: Path) -> None:
            fd = writable_dir(destination)
            member = os.open("EXTRA", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o444, dir_fd=fd)
            os.close(member)
            os.fchmod(fd, 0o555)
            os.close(fd)

        def replace_destination(destination: Path, symlink: bool) -> None:
            # macOS renameatx/rename requires the source directory itself to be
            # owner-writable; a same-UID process can restore that permission.
            destination.chmod(0o755)
            destination.rename(destination.with_name("retained-original"))
            if symlink:
                destination.symlink_to("retained-original", target_is_directory=True)
            else:
                destination.write_text("post-snapshot replacement\n", encoding="utf-8")

        mutations = {
            "regular-replacement": lambda path: replace_member(path, False),
            "symlink-replacement": lambda path: replace_member(path, True),
            "same-inode-content": mutate_content,
            "extra-entry": add_extra,
            "file-mode": lambda path: (path / "CASE.json").chmod(0o644),
            "directory-mode": lambda path: path.chmod(0o755),
            "destination-regular": lambda path: replace_destination(path, False),
            "destination-symlink": lambda path: replace_destination(path, True),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as temp:
                trusted = Path(temp).resolve()
                destination = trusted / "case"
                real_verify = stage._verify_published_path
                snapshots = 0

                def mutate_after_snapshot(*args, **kwargs) -> None:
                    nonlocal snapshots
                    real_verify(*args, **kwargs)
                    snapshots += 1
                    if snapshots == 2:
                        mutate(destination)

                with mock.patch("benchmarks.stage._verify_published_path",
                                side_effect=mutate_after_snapshot):
                    result = stage.stage_case(case_id, destination, staging_root=trusted)
                self.assertEqual(destination, result)

    def test_rename_effect_then_error_is_resolved_as_success(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            real_publish = stage._publish_directory_noreplace

            def publish_then_error(parent_fd: int, source: str, final: str) -> None:
                real_publish(parent_fd, source, final)
                raise OSError("injected report after successful rename")

            with mock.patch("benchmarks.stage._publish_directory_noreplace",
                            side_effect=publish_then_error):
                result = stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertEqual({"CASE.json", "TASK.md"}, {item.name for item in result.iterdir()})

    def test_ctypes_argument_error_is_wrapped_and_publish_residue_is_retained(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            with mock.patch("benchmarks.stage._publish_directory_noreplace",
                            side_effect=ctypes.ArgumentError("bad native argument")):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertEqual(1, len(list(trusted.iterdir())))

    def test_manifest_is_loaded_once_for_staging(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            with mock.patch("benchmarks.stage.load_manifest", wraps=stage.load_manifest) as read:
                stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertEqual(1, read.call_count)

    def test_relative_staging_root_is_rejected(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with self.assertRaises(stage.BenchmarkError):
            stage.stage_case(case_id, Path.cwd() / "case", staging_root=Path("."))

    def test_unsupported_atomic_publish_fails_closed_without_fallback(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        with tempfile.TemporaryDirectory() as temp:
            trusted = Path(temp).resolve()
            with mock.patch("benchmarks.stage.sys.platform", "unsupported-os"):
                with self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
            self.assertEqual(1, len(list(trusted.iterdir())))

    def test_fstat_or_fsync_failure_is_wrapped_without_name_based_cleanup(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        for failure in ("fstat", "fsync"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                trusted = Path(temp).resolve()
                if failure == "fsync":
                    patcher = mock.patch("benchmarks.stage.os.fsync",
                                         side_effect=OSError("injected fsync failure"))
                else:
                    real_fstat = os.fstat
                    failed = False

                    def fail_bundle_fstat(fd: int):
                        nonlocal failed
                        result = real_fstat(fd)
                        if stat.S_ISDIR(result.st_mode) and not failed:
                            failed = True
                            raise OSError("injected fstat failure")
                        return result

                    patcher = mock.patch("benchmarks.stage.os.fstat", side_effect=fail_bundle_fstat)
                with patcher, self.assertRaises(stage.BenchmarkError):
                    stage.stage_case(case_id, trusted / "case", staging_root=trusted)
                observed = list(trusted.iterdir())
                if failure == "fstat":
                    # The injected failure can occur while opening the absolute
                    # ancestry, before private construction begins.
                    self.assertEqual([], observed)
                else:
                    self.assertEqual(1, len(observed))
                    self.assertTrue(observed[0].name.startswith(".pangea-benchmark-"))

    def test_mkdir_open_and_member_fstat_or_stat_failures_retain_auditable_residue(self) -> None:
        case_id = next(iter(EXPECTED_CASES))
        failure_modes = ("mkdir-effect-error", "private-open", "private-stat",
                         "member-fstat", "member-stat")
        for failure_mode in failure_modes:
            with self.subTest(failure=failure_mode), tempfile.TemporaryDirectory() as temp:
                trusted = Path(temp).resolve()
                real_open = os.open
                real_mkdir = os.mkdir
                real_stat = os.stat
                real_fstat = os.fstat
                regular_fstats = 0

                def injected_mkdir(path, *args, **kwargs):
                    result = real_mkdir(path, *args, **kwargs)
                    if (failure_mode == "mkdir-effect-error" and isinstance(path, str)
                            and path.startswith(".pangea-benchmark-")):
                        raise OSError("injected mkdir effect-then-error")
                    return result

                def injected_open(path, *args, **kwargs):
                    if (failure_mode == "private-open" and isinstance(path, str)
                            and path.startswith(".pangea-benchmark-")):
                        raise OSError("injected private open failure")
                    return real_open(path, *args, **kwargs)

                def injected_stat(path, *args, **kwargs):
                    if (failure_mode == "private-stat" and isinstance(path, str)
                            and path.startswith(".pangea-benchmark-")):
                        raise OSError("injected private stat failure")
                    if failure_mode == "member-stat" and path == "CASE.json":
                        raise OSError("injected member stat failure")
                    return real_stat(path, *args, **kwargs)

                def injected_fstat(fd: int):
                    nonlocal regular_fstats
                    result = real_fstat(fd)
                    if stat.S_ISREG(result.st_mode):
                        regular_fstats += 1
                        # load_manifest performs two regular-file fstats; the
                        # third regular descriptor is the first bundle member.
                        if failure_mode == "member-fstat" and regular_fstats == 3:
                            raise OSError("injected member fstat failure")
                    return result

                with mock.patch("benchmarks.stage.os.mkdir", side_effect=injected_mkdir), \
                        mock.patch("benchmarks.stage.os.open", side_effect=injected_open), \
                        mock.patch("benchmarks.stage.os.stat", side_effect=injected_stat), \
                        mock.patch("benchmarks.stage.os.fstat", side_effect=injected_fstat):
                    with self.assertRaises(stage.BenchmarkError):
                        stage.stage_case(case_id, trusted / "case", staging_root=trusted)
                residue = list(trusted.iterdir())
                self.assertEqual(1, len(residue))
                self.assertTrue(residue[0].name.startswith(".pangea-benchmark-"))


if __name__ == "__main__":
    unittest.main()
