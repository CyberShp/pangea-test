from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime import library_runtime


class LibraryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = self.root / "pangea-data/library/catalog.jsonl"
        self.catalog.parent.mkdir(parents=True)
        markdown = self.root / "pangea-data/library/markdown/a.md"
        markdown.parent.mkdir(parents=True)
        markdown.write_text("# Cmd resources\n\n<!-- page:unknown paragraph:3 -->\nCMD quota returns after overload recovery.\n", encoding="utf-8")
        self.rows = [
            {"record_type": "input_source", "source_path": "specs/资源设计.docx", "sha256": "aaa", "markdown_path": "library/markdown/a.md", "conversion_status": "converted"},
            {"record_type": "input_source", "source_path": "copies/资源设计.docx", "sha256": "aaa", "markdown_path": "library/markdown/a.md", "conversion_status": "converted"},
        ]
        self.catalog.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def classification(self) -> dict[str, object]:
        return {"role": "design", "tags": ["resource", "iscsi"], "summary": "CMD resource recovery", "applicable_modules": ["iscsi"], "versions": ["v1"], "confidence": "medium", "rationale": "Document title and converted text describe quota recovery."}

    def records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.catalog.read_text(encoding="utf-8").splitlines()]

    def test_hints_are_incremental_and_same_hash_inherits_classification(self) -> None:
        first = library_runtime.refresh_role_hints(self.root)
        self.assertEqual(2, first["updated"])
        library_runtime.write_semantic_classification(self.root, "specs/资源设计.docx", self.classification())
        rows = self.records(); rows[1].pop("classification_sha256", None)
        self.catalog.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        result = library_runtime.refresh_role_hints(self.root)
        self.assertEqual(1, result["inherited"])
        copied = self.records()[1]["semantic_classification"]
        self.assertTrue(copied["inherited"])
        self.assertEqual("specs/资源设计.docx", copied["inherited_from"])
        self.assertFalse(copied["source_backed"])
        self.assertEqual(0, library_runtime.refresh_role_hints(self.root)["updated"])

    def test_search_returns_bounded_markdown_snippets_and_filters(self) -> None:
        library_runtime.refresh_role_hints(self.root)
        library_runtime.write_semantic_classification(self.root, "specs/资源设计.docx", self.classification())
        result = library_runtime.search_library(self.root, "quota recovery", role="design", tags=["resource"], module="iscsi", version="v1", limit=1)
        self.assertEqual(1, result["count"])
        hit = result["results"][0]
        self.assertEqual("aaa", hit["sha256"])
        self.assertEqual("page:unknown paragraph:3", hit["markdown_anchor"])
        self.assertLessEqual(len(hit["snippet"]), 370)

    def test_semantic_inference_cannot_claim_source_backed_fact(self) -> None:
        invalid = self.classification(); invalid["source_backed"] = True
        with self.assertRaisesRegex(library_runtime.LibraryRuntimeError, "不能将模型推断伪装成事实"):
            library_runtime.write_semantic_classification(self.root, "specs/资源设计.docx", invalid)

    def test_library_never_moves_inbox_or_legacy_content(self) -> None:
        inbox = self.root / "pangea-data/inbox/keep.txt"; inbox.parent.mkdir(parents=True); inbox.write_text("keep", encoding="utf-8")
        legacy = self.root / "source/old.txt"; legacy.parent.mkdir(parents=True); legacy.write_text("old", encoding="utf-8")
        (self.root / "inputs").mkdir(); (self.root / "inputs/README.md").write_text("keep", encoding="utf-8")
        gaps = library_runtime.legacy_migration_gaps(self.root)
        self.assertEqual([{"legacy_root": "source", "path": "source/old.txt", "kind": "file"}], gaps["legacy_migration_gaps"])
        library_runtime.refresh_role_hints(self.root)
        self.assertEqual("keep", inbox.read_text(encoding="utf-8"))
        self.assertTrue(legacy.exists())


if __name__ == "__main__":
    unittest.main()
