from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarks import stage


class BenchmarkManifestTests(unittest.TestCase):
    def test_manifest_and_oracles_are_complete_and_traceable(self) -> None:
        self.assertEqual([], stage.validate_manifest())
        manifest = stage.load_manifest()
        self.assertGreaterEqual(len(manifest["cases"]), 4)
        repositories = {case["repository"].rstrip("/").split("/")[-1] for case in manifest["cases"]}
        self.assertTrue({"spdk", "ucx", "rdma-core", "bmcweb"}.issubset(repositories))
        for case in manifest["cases"]:
            self.assertIn(case["source"]["kind"], {"issue", "pull_request"})
            self.assertTrue(case["source"]["url"].startswith("https://github.com/"))
            self.assertEqual(40, len(case["revision"]["base_commit"]))

    def test_staging_contains_no_oracle_or_scoring_answers(self) -> None:
        manifest = stage.load_manifest()
        for case in manifest["cases"]:
            with self.subTest(case=case["id"]), tempfile.TemporaryDirectory() as temp:
                destination = stage.stage_case(case["id"], Path(temp))
                self.assertEqual({"TASK.md", "case.json"}, {path.name for path in destination.iterdir()})
                staged = json.loads((destination / "case.json").read_text(encoding="utf-8"))
                self.assertEqual(case, staged)
                public_text = "\n".join(
                    path.read_text(encoding="utf-8") for path in destination.iterdir()
                )
                oracle = json.loads(
                    (stage.ORACLE_DIRECTORY / f"{case['id']}.json").read_text(encoding="utf-8")
                )
                # A public PR/Issue may naturally contain a symptom keyword. The
                # hidden contract is the full oracle and its weighted rubric.
                self.assertNotIn('"scoring"', public_text)
                self.assertNotIn('"fault_mode"', public_text)
                self.assertNotIn('"evidence_keywords"', public_text)
                self.assertNotIn(oracle["fault_mode"], public_text)

    def test_unknown_case_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(stage.BenchmarkError):
                stage.stage_case("not-a-case", Path(temp))


if __name__ == "__main__":
    unittest.main()
