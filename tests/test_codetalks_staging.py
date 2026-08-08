from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from evaluation.codetalks_staging import (
    ADAPTER_RELATIVE_PATH, ARCHIVE_PATH, ARCHIVE_ROOT, CodeTalksStagingError, EXPECTED_FILE_COUNT,
    MANIFEST_NAME, OUTPUT_RELATIVE_ROOT, collect_final_output, materialize_candidate,
)


def make_archive(
    path: Path, extras: list[tuple[str, bytes, int | None]] | None = None,
    skill: str = "---\nname: codetalks-source-driven-blackbox-v2\nversion: 2.4.0\n---\n# CodeTalks\n",
) -> str:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr(f"{ARCHIVE_ROOT}SKILL.md", skill)
        for index in range(EXPECTED_FILE_COUNT - 1):
            package.writestr(f"{ARCHIVE_ROOT}part-{index:02}.md", f"part {index}\n")
        for name, payload, mode in extras or []:
            info = zipfile.ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            package.writestr(info, payload)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CodeTalksStagingTests(unittest.TestCase):
    def test_materializes_complete_readonly_candidate_and_minimal_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); archive = root / "candidate.zip"; digest = make_archive(archive)
            with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                manifest = materialize_candidate(root / "stage", archive)
            stage = root / "stage"; skill = stage / ".opencode/skills/codetalks-source-driven-blackbox-v2"
            self.assertEqual(EXPECTED_FILE_COUNT, len(manifest["skill"]["files"]))
            self.assertFalse(any(str(root).encode() in item.read_bytes() for item in stage.rglob("*") if item.is_file()))
            self.assertFalse((skill / "part-00.md").stat().st_mode & stat.S_IWUSR)
            adapter = (stage / ADAPTER_RELATIVE_PATH).read_text(encoding="utf-8")
            self.assertIn("Load and follow `.opencode/skills/codetalks-source-driven-blackbox-v2/SKILL.md`", adapter)
            self.assertNotIn("PANGEA", adapter.upper())
            self.assertEqual("codetalks-source-driven-blackbox-v2", manifest["skill"]["name"])
            self.assertEqual("2.4.0", manifest["skill"]["version"])
            self.assertEqual(digest, json.loads((stage / MANIFEST_NAME).read_text())["archive"]["sha256"])
            debug = subprocess.run(
                ["opencode", "debug", "agent", "codetalks-fused-v2.4", "--pure"],
                cwd=stage, check=True, capture_output=True, text=True,
            )
            parsed = json.loads(debug.stdout)
            self.assertEqual("primary", parsed["mode"])
            self.assertEqual(
                {"bash": True, "read": True, "write": True, "skill": True, "question": False},
                {name: parsed["tools"][name] for name in ("bash", "read", "write", "skill", "question")},
            )

    def test_hash_mismatch_and_invalid_members_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); archive = root / "candidate.zip"; make_archive(archive)
            with self.assertRaisesRegex(CodeTalksStagingError, "SHA-256"):
                materialize_candidate(root / "bad", archive)
            for suffix, mode, error in [("../outside.md", None, "clean relative"), ("link", stat.S_IFLNK | 0o777, "symlink")]:
                candidate = root / f"{len(suffix)}.zip"; digest = make_archive(candidate, [(ARCHIVE_ROOT + suffix, b"x", mode)])
                with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                    with self.assertRaisesRegex(CodeTalksStagingError, error):
                        materialize_candidate(root / f"dest-{len(suffix)}", candidate)

    def test_duplicate_member_and_missing_skill_file_count_fail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); archive = root / "duplicate.zip"
            with zipfile.ZipFile(archive, "w") as package:
                for index in range(EXPECTED_FILE_COUNT - 1):
                    package.writestr(f"{ARCHIVE_ROOT}part-{index}.md", "x")
                package.writestr(f"{ARCHIVE_ROOT}same.md", "a"); package.writestr(f"{ARCHIVE_ROOT}same.md", "b")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                with self.assertRaisesRegex(CodeTalksStagingError, "duplicate"):
                    materialize_candidate(root / "stage", archive)
            missing = root / "missing-skill.zip"
            with zipfile.ZipFile(missing, "w") as package:
                for index in range(EXPECTED_FILE_COUNT):
                    package.writestr(f"{ARCHIVE_ROOT}part-{index}.md", "x")
            missing_digest = hashlib.sha256(missing.read_bytes()).hexdigest()
            with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", missing_digest):
                with self.assertRaisesRegex(CodeTalksStagingError, "SKILL.md"):
                    materialize_candidate(root / "missing-stage", missing)

    def test_skill_frontmatter_identity_is_required(self) -> None:
        for skill, error in [
            ("---\nname: wrong\nversion: 2.4.0\n---\n", "name"),
            ("---\nname: codetalks-source-driven-blackbox-v2\nversion: 2.4\n---\n", "version"),
            ("# no frontmatter\n", "frontmatter"),
            ("---\nname: codetalks-source-driven-blackbox-v2\nname: duplicate\nversion: 2.4.0\n---\n", "duplicate"),
            ("---\nname: [codetalks-source-driven-blackbox-v2]\nversion: 2.4.0\n---\n", "scalar string"),
            ("---\nname: [\nversion: 2.4.0\n---\n", "invalid"),
        ]:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as raw:
                root = Path(raw); archive = root / "candidate.zip"; digest = make_archive(archive, skill=skill)
                with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                    with self.assertRaisesRegex(CodeTalksStagingError, error):
                        materialize_candidate(root / "stage", archive)

    def test_multiline_description_frontmatter_is_accepted(self) -> None:
        skill = ("---\nname: codetalks-source-driven-blackbox-v2\ndescription: >\n"
                 "  A multiline description that remains metadata.\n"
                 "  It must not change the scalar identity fields.\nversion: 2.4.0\n---\n# CodeTalks\n")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); archive = root / "candidate.zip"; digest = make_archive(archive, skill=skill)
            with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                manifest = materialize_candidate(root / "stage", archive)
            self.assertEqual("2.4.0", manifest["skill"]["version"])

    @unittest.skipUnless(ARCHIVE_PATH.is_file(), "frozen CodeTalks archive is not available locally")
    def test_frozen_archive_materializes_and_primary_adapter_parses(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            stage = Path(raw) / "stage"
            manifest = materialize_candidate(stage)
            self.assertEqual("codetalks-source-driven-blackbox-v2", manifest["skill"]["name"])
            debug = subprocess.run(
                ["opencode", "debug", "agent", "codetalks-fused-v2.4", "--pure"],
                cwd=stage, check=True, capture_output=True, text=True,
            )
            self.assertEqual("primary", json.loads(debug.stdout)["mode"])

    def test_collector_binds_only_controlled_members_or_native_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "candidate"; archive = Path(raw) / "candidate.zip"; digest = make_archive(archive)
            with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                materialized = materialize_candidate(root, archive)
            evaluator = root.parent / f"evaluator-{root.name}"; evaluator.mkdir()
            receipt = collect_final_output(root, "native final", evaluator_root=evaluator,
                                           expected_manifest_sha256=materialized["manifest_sha256"], expected_materialization=materialized)
            self.assertIn("native_final_text_sha256", receipt)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "candidate"; archive = Path(raw) / "candidate.zip"; digest = make_archive(archive)
            with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                materialized = materialize_candidate(root, archive)
            output = root / OUTPUT_RELATIVE_ROOT
            evaluator = root.parent / f"evaluator-{root.name}"; evaluator.mkdir()
            (output / "answer.md").write_text("answer", encoding="utf-8")
            self.assertIn("files", collect_final_output(root, evaluator_root=evaluator,
                                                          expected_manifest_sha256=materialized["manifest_sha256"], expected_materialization=materialized))
            (output / "extra.txt").write_text("no", encoding="utf-8")
            with self.assertRaisesRegex(CodeTalksStagingError, "Markdown or JSON"):
                collect_final_output(root, evaluator_root=evaluator,
                                     expected_manifest_sha256=materialized["manifest_sha256"], expected_materialization=materialized)

    def test_collector_rejects_wrong_or_replaced_evaluator_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "candidate"; archive = Path(raw) / "candidate.zip"; digest = make_archive(archive)
            with patch("evaluation.codetalks_staging.ARCHIVE_SHA256", digest):
                materialized = materialize_candidate(root, archive)
            evaluator = Path(raw) / "evaluator"; evaluator.mkdir()
            with self.assertRaisesRegex(CodeTalksStagingError, "differs"):
                collect_final_output(root, "native", evaluator_root=evaluator,
                                     expected_manifest_sha256="0" * 64, expected_materialization=materialized)
            manifest = root / MANIFEST_NAME; manifest.chmod(0o644); manifest.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(CodeTalksStagingError, "differs"):
                collect_final_output(root, "native", evaluator_root=evaluator,
                                     expected_manifest_sha256=materialized["manifest_sha256"], expected_materialization=materialized)
