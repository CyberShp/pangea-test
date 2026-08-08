from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from runtime import diagnostic_artifacts as da


class DiagnosticArtifactTests(unittest.TestCase):
    RUN_ID = "triage-01"

    def _run(self, root: Path) -> Path:
        run = root / "pangea-data" / "runs" / self.RUN_ID
        (run / "internal").mkdir(parents=True)
        (run / "manifest.json").write_text(json.dumps({"run_id": self.RUN_ID}), encoding="utf-8")
        return run

    def _artifact(self, kind: str) -> dict[str, object]:
        common: dict[str, object] = {
            "artifact_type": kind, "input_sha256": {"log_summary": "a", "pcap_summary": "b", "failure_classification": "c"}[kind] * 64,
            "status": "complete", "tool": {"name": "fixture parser", "version": "1"},
        }
        if kind in {"log_summary", "pcap_summary"}:
            prefix = "log:" if kind == "log_summary" else "pkt:"
            common.update({"timeline": [{"raw_ref": prefix + "1", "summary": "first observed event"}],
                           "key_signals": [], "correlations": [], "raw_excerpts": []})
        else:
            common.update({"test_case_id": "TC-001", "conclusion": "product_defect", "confidence": "high",
                           "basis": "observed behavior violates the bound specification",
                           "evidence": [{"raw_ref": "log:1", "summary": "stable product failure"}],
                           "next_action": "file a defect and execute the bound regression"})
        return common

    def _assert_committed(self, root: Path, kinds: set[str]) -> None:
        run = root / "pangea-data/runs" / self.RUN_ID
        index = json.loads((run / "internal/diagnostic-artifacts.json").read_text())
        self.assertEqual(kinds, set(index["artifacts"]))
        for kind in kinds:
            record = index["artifacts"][kind]
            target = run / record["path"]
            self.assertTrue(target.is_file())
            self.assertEqual(record["content_sha256"], da._sha(target.read_bytes()))
            self.assertEqual("committed", record["state"])

    def test_fixed_paths_are_idempotent_and_conflicts_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root)
            artifact = self._artifact("log_summary")
            first = da.write_artifact(root, self.RUN_ID, artifact)
            self.assertEqual(first, da.write_artifact(root, self.RUN_ID, artifact))
            changed = copy.deepcopy(artifact); changed["timeline"][0]["summary"] = "different observed event"  # type: ignore[index]
            with self.assertRaises(da.DiagnosticArtifactError):
                da.write_artifact(root, self.RUN_ID, changed)
            self._assert_committed(root, {"log_summary"})

    def test_schema_is_closed_and_has_minimum_semantics(self) -> None:
        import jsonschema
        schema = json.loads((Path(__file__).parents[1] / "schemas/diagnostic-artifact.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        mutations = []
        for kind in da.ARTIFACT_PATHS:
            base = self._artifact(kind); base["run_id"] = self.RUN_ID; base["schema_version"] = "1.0"
            mutations.append(dict(base, private_answer="secret"))
            bad_hash = dict(base); bad_hash["input_sha256"] = "x" * 64; mutations.append(bad_hash)
            if kind in {"log_summary", "pcap_summary"}:
                empty = copy.deepcopy(base); empty["timeline"] = []; mutations.append(empty)
            else:
                empty = copy.deepcopy(base); empty["evidence"] = []; mutations.append(empty)
        for value in mutations:
            with self.subTest(kind=value["artifact_type"]):
                with self.assertRaises(da.DiagnosticArtifactError): da.validate_artifact(value)
                self.assertTrue(list(validator.iter_errors(value)))

    def test_external_diagnostics_symlink_and_ancestor_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp); run = self._run(root)
            (run / "internal/diagnostics").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
            self.assertEqual([], list(Path(outside).iterdir()))
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp); (root / "pangea-data").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))

    def test_target_index_journal_and_lock_special_files_are_rejected(self) -> None:
        cases = tuple((relative, kind) for relative in (
            "diagnostics/log-summary.json", "diagnostic-artifacts.json", ".diagnostic-artifacts.journal.json",
        ) for kind in ("symlink", "fifo", "hardlink")) + ((".diagnostic-artifacts.lock", "hardlink"),)
        for relative, kind in cases:
            with self.subTest(relative=relative, kind=kind), tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
                root = Path(temp); run = self._run(root); internal = run / "internal"; (internal / "diagnostics").mkdir()
                target = internal / relative
                if kind == "symlink": target.symlink_to(Path(outside) / "escape")
                elif kind == "fifo": os.mkfifo(target)
                else:
                    source = Path(outside) / "shared"; source.write_text("x"); os.link(source, target)
                with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))

    def test_concurrent_three_types_do_not_lose_index_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root); errors: list[BaseException] = []
            def submit(kind: str) -> None:
                try: da.write_artifact(root, self.RUN_ID, self._artifact(kind))
                except BaseException as exc: errors.append(exc)
            threads = [threading.Thread(target=submit, args=(kind,)) for kind in da.ARTIFACT_PATHS]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual([], errors)
            self._assert_committed(root, set(da.ARTIFACT_PATHS))

    def test_corrupt_index_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = self._run(root)
            (run / "internal/diagnostic-artifacts.json").write_text("not-json")
            with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))

    def test_unindexed_identical_target_is_not_adopted_and_missing_index_target_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = self._run(root); body = self._artifact("log_summary")
            body.update({"run_id": self.RUN_ID, "schema_version": "1.0"})
            target = run / "internal/diagnostics/log-summary.json"; target.parent.mkdir()
            target.write_bytes(da._pretty(body))
            with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = self._run(root)
            da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
            (run / "internal/diagnostics/log-summary.json").unlink()
            (run / "internal/.diagnostic-artifacts.journal.json").unlink()
            with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))

    def test_effect_then_error_is_recognized_and_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root); real = da._rename_noreplace
            calls = 0
            def effect_then_error(fd: int, source: str, destination: str) -> None:
                nonlocal calls
                real(fd, source, destination); calls += 1
                if calls == 1: raise OSError("reported after effect")
            with mock.patch.object(da, "_rename_noreplace", side_effect=effect_then_error):
                da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
            self._assert_committed(root, {"log_summary"})

    def test_replace_effect_then_error_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root); real = os.replace; injected = False
            def effect_then_error(source: str, destination: str, **kwargs: object) -> None:
                nonlocal injected
                real(source, destination, **kwargs)
                if destination == da.INDEX_NAME and not injected:
                    injected = True
                    raise OSError("index replace reported after effect")
            with mock.patch.object(da.os, "replace", side_effect=effect_then_error):
                da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
            self._assert_committed(root, {"log_summary"})

    def test_named_transaction_states_recover(self) -> None:
        scenarios = ("after_prepared", "after_artifact", "after_index", "after_committed")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); self._run(root)
                real_journal, real_replace, real_publish = da._write_journal, da._replace, da._publish_new
                tripped = False
                def journal(managed: da._ManagedRun, value: dict[str, object]) -> None:
                    nonlocal tripped
                    real_journal(managed, value)
                    if not tripped and ((scenario == "after_prepared" and value["state"] == "prepared")
                                        or (scenario == "after_artifact" and value["state"] == "artifact_published")
                                        or (scenario == "after_committed" and value["state"] == "committed")):
                        tripped = True; raise OSError("journal boundary")
                def replace(fd: int, name: str, content: bytes, prefix: str, verify: object) -> None:
                    nonlocal tripped
                    real_replace(fd, name, content, prefix, verify)  # type: ignore[arg-type]
                    if not tripped and scenario == "after_index" and name == da.INDEX_NAME:
                        tripped = True; raise OSError("index boundary")
                with mock.patch.object(da, "_write_journal", side_effect=journal), mock.patch.object(da, "_replace", side_effect=replace):
                    with self.assertRaises(da.DiagnosticArtifactError):
                        da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
                self.assertTrue(tripped)
                da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
                self._assert_committed(root, {"log_summary"})

    def test_each_transaction_boundary_recovers_idempotently(self) -> None:
        # Fail one fsync boundary at a time.  A second call must either finish
        # the journal or safely start the same idempotent transaction.
        for fail_at in range(1, 10):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); self._run(root); real = os.fsync; calls = 0
                def injected(fd: int) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == fail_at: raise OSError("boundary failure")
                    real(fd)
                with mock.patch.object(da.os, "fsync", side_effect=injected):
                    try: da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
                    except da.DiagnosticArtifactError: pass
                da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))
                self._assert_committed(root, {"log_summary"})

    def test_partial_temp_write_is_removed_before_error_returns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); parent = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)); real = os.write
            calls = 0
            def partial_then_error(fd: int, value: object) -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real(fd, memoryview(value)[:1])  # type: ignore[arg-type]
                raise OSError("deterministic partial write")
            try:
                with mock.patch.object(da.os, "write", side_effect=partial_then_error):
                    with self.assertRaises(da.DiagnosticArtifactError): da._temp_write(parent, "cleanup", b"payload")
                self.assertEqual([], list(root.iterdir()))
            finally:
                os.close(parent)

    def test_directory_replacement_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = self._run(root); real = da._write_journal
            def replace_after_journal(managed: da._ManagedRun, journal: dict[str, object]) -> None:
                real(managed, journal)
                internal = run / "internal"; moved = run / "internal-old"; internal.rename(moved); internal.mkdir()
            with mock.patch.object(da, "_write_journal", side_effect=replace_after_journal):
                with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))

    def test_diagnostics_directory_replacement_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = self._run(root); real = da._write_journal
            def replace_after_journal(managed: da._ManagedRun, journal: dict[str, object]) -> None:
                real(managed, journal)
                diagnostics = run / "internal/diagnostics"; moved = run / "internal/diagnostics-old"
                diagnostics.rename(moved); diagnostics.mkdir()
            with mock.patch.object(da, "_write_journal", side_effect=replace_after_journal):
                with self.assertRaises(da.DiagnosticArtifactError): da.write_artifact(root, self.RUN_ID, self._artifact("log_summary"))


if __name__ == "__main__":
    unittest.main()
