from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from runtime.converters import ConversionResult, OutputSecurityError, convert_document, write_markdown


class ConverterTests(unittest.TestCase):
    def zip(self, root: Path, name: str, files: dict[str, str | bytes]) -> Path:
        path = root / name
        with zipfile.ZipFile(path, "w") as archive:
            for key, value in files.items(): archive.writestr(key, value)
        return path

    def test_docx_fallback_keeps_paragraph_anchor_and_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = self.zip(root, "note.docx", {
                "word/document.xml": '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>hello</w:t></w:r></w:p></w:body></w:document>',
                "word/media/image1.png": b"not-a-real-image",
            })
            result = convert_document(doc, root / "out")
            self.assertIn("page:unknown paragraph:1", result.markdown)
            self.assertIn("hello", result.markdown)
            self.assertEqual(1, len(result.assets))
            self.assertIn("[未解析视觉证据: 全局未定位 / image1.png]", result.markdown)

    def test_docx_explicit_page_break_updates_page_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = self.zip(root, "pages.docx", {
                "word/document.xml": '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>第一页</w:t><w:br w:type="page"/></w:r></w:p><w:p><w:r><w:t>第二页</w:t></w:r></w:p></w:body></w:document>',
            })
            markdown = convert_document(doc).markdown
            self.assertIn("page:1 paragraph:1", markdown)
            self.assertIn("page:2 paragraph:2", markdown)

    def test_xlsx_fallback_keeps_sheet_and_cell_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xlsx = self.zip(root, "table.xlsx", {
                "xl/workbook.xml": '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Cases" r:id="rId1"/></sheets></workbook>',
                "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
                "xl/worksheets/sheet1.xml": '<worksheet><sheetData><row><c r="B2"><v>ready</v></c></row></sheetData></worksheet>',
            })
            result = convert_document(xlsx)
            self.assertIn("sheet:Cases", result.markdown)
            self.assertIn("`Cases!B2`: ready", result.markdown)

    def test_pptx_and_pdf_pending_without_local_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pptx = self.zip(root, "deck.pptx", {"ppt/slides/slide1.xml": '<p:sld xmlns:p="p"><p:p><p:r><p:t>one</p:t></p:r></p:p></p:sld>'})
            self.assertIn("slide:1", convert_document(pptx).markdown)
            pdf = root / "paper.pdf"; pdf.write_bytes(b"%PDF")
            with patch("runtime.converters.shutil.which", return_value=None):
                result = convert_document(pdf)
            self.assertEqual("pending", result.status)
            self.assertIn("未伪转换", result.markdown)

    def test_pdf_pdftotext_adds_page_anchors_and_cleans_temporary_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "paper.pdf"; pdf.write_bytes(b"%PDF")
            created: list[Path] = []

            def fake_run(command, **kwargs):
                output = Path(command[-1]); created.append(output)
                output.write_text("first\fsecond\f", encoding="utf-8")
                return __import__("subprocess").CompletedProcess(command, 0)

            with patch("runtime.converters.shutil.which", return_value="/tools/pdftotext"), patch("runtime.converters.subprocess.run", side_effect=fake_run) as run:
                result = convert_document(pdf)
            self.assertEqual("converted", result.status)
            self.assertIn("<!-- page:1 -->", result.markdown)
            self.assertIn("<!-- page:2 -->", result.markdown)
            self.assertTrue(all(not path.parent.exists() for path in created))
            self.assertEqual(__import__("subprocess").DEVNULL, run.call_args.kwargs["stdin"])
            self.assertEqual(30, run.call_args.kwargs["timeout"])

    def test_pdf_timeout_and_old_office_are_honest_pending_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "paper.pdf"; pdf.write_bytes(b"%PDF")
            old_doc = root / "legacy.doc"; old_doc.write_bytes(b"binary")
            with patch("runtime.converters.shutil.which", return_value="/tools/pdftotext"), patch("runtime.converters.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("pdftotext", 30)):
                result = convert_document(pdf)
            self.assertEqual("pending", result.status)
            self.assertIn("30 秒", result.markdown)
            legacy = convert_document(old_doc)
            self.assertEqual("pending", legacy.status)
            self.assertIn("LibreOffice", legacy.markdown)

    def test_text_and_csv_preserve_source_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = root / "notes.txt"; text.write_text("one\ntwo\n", encoding="utf-8")
            csv_file = root / "cases.csv"; csv_file.write_text("name,result\nrecovery,pass\n", encoding="utf-8")
            self.assertIn("source:notes.txt line:2", convert_document(text).markdown)
            csv_markdown = convert_document(csv_file).markdown
            self.assertIn("source:cases.csv row:2", csv_markdown)
            self.assertIn("| name | result |", csv_markdown)

    def test_pptx_media_is_attributed_to_slide_when_relationship_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pptx = self.zip(root, "visual.pptx", {
                "ppt/slides/slide1.xml": '<p:sld xmlns:p="p"><p:p><p:r><p:t>one</p:t></p:r></p:p></p:sld>',
                "ppt/slides/_rels/slide1.xml.rels": '<Relationships><Relationship Id="rId1" Target="../media/image1.png"/></Relationships>',
                "ppt/media/image1.png": b"image",
            })
            markdown = convert_document(pptx, root / "out").markdown
            self.assertIn("[未解析视觉证据: 幻灯片 1 / image1.png]", markdown)

    def test_markdown_and_asset_outputs_reject_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root / "external.txt"
            external.write_text("keep", encoding="utf-8")
            markdown = root / "result.md"
            markdown.symlink_to(external)
            with self.assertRaisesRegex(OutputSecurityError, "非普通输出目标"):
                write_markdown(ConversionResult(root / "source.txt", "changed"), markdown, managed_root=root)
            self.assertEqual("keep", external.read_text(encoding="utf-8"))

            doc = self.zip(root, "linked.docx", {
                "word/document.xml": '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>hello</w:t></w:r></w:p></w:body></w:document>',
                "word/media/image1.png": b"changed",
            })
            assets = root / "out" / "assets"
            assets.mkdir(parents=True)
            (assets / "image1.png").symlink_to(external)
            with self.assertRaisesRegex(OutputSecurityError, "非普通输出目标"):
                convert_document(doc, root / "out", managed_root=root)
            self.assertEqual("keep", external.read_text(encoding="utf-8"))


if __name__ == "__main__": unittest.main()
