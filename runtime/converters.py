"""Offline conversion of team reference documents into traceable Markdown."""
from __future__ import annotations

import csv
import os
import posixpath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


class ConversionError(RuntimeError):
    pass


class OutputSecurityError(ConversionError):
    pass


PDF_TIMEOUT_SECONDS = 30
MAX_PDF_TEXT_BYTES = 16 * 1024 * 1024

@dataclass(frozen=True)
class ConversionResult:
    source: Path
    markdown: str
    status: str = "converted"
    assets: tuple[Path, ...] = ()


def convert_document(
    source: str | Path,
    output_dir: str | Path | None = None,
    *,
    managed_root: str | Path | None = None,
) -> ConversionResult:
    """Convert locally readable documents without modifying their source files."""
    path = Path(source)
    suffix = path.suffix.lower()
    if not path.is_file():
        raise ConversionError(f"文件不存在: {path}")
    if suffix == ".pdf":
        return _pdf(path)
    if suffix in {".doc", ".xls", ".ppt"}:
        return _pending_legacy_office(path)
    if suffix in {".md", ".txt"}:
        return ConversionResult(path, _plain_text(path))
    if suffix == ".csv":
        return ConversionResult(path, _csv(path))
    if suffix not in {".docx", ".xlsx", ".pptx"}:
        raise ConversionError(f"不支持的文档类型: {suffix or path.name}")
    if output_dir:
        assets_root, normalized_output = _output_context(Path(output_dir), managed_root)
        assets_dir = normalized_output / "assets"
    else:
        assets_root = None
        assets_dir = None
    if suffix == ".docx":
        text, assets = _docx(path, assets_dir, assets_root)
    elif suffix == ".xlsx":
        text, assets = _xlsx(path, assets_dir, assets_root)
    else:
        text, assets = _pptx(path, assets_dir, assets_root)
    return ConversionResult(path, text, assets=tuple(assets))


def _pending(path: Path, title: str, reason: str) -> ConversionResult:
    return ConversionResult(path, f"# 待转换 {title}\n\n<!-- source:{path.name} -->\n\n> {reason}\n", "pending")


def _pending_legacy_office(path: Path) -> ConversionResult:
    return _pending(
        path,
        f"旧 Office 文件：{path.name}",
        "旧二进制 Office 格式需要本地 LibreOffice 或受控离线转换；当前未执行转换，未伪转换。",
    )


def _pdf(path: Path) -> ConversionResult:
    executable = shutil.which("pdftotext")
    if not executable:
        return _pending(path, f"PDF：{path.name}", "缺少本地 pdftotext；当前未安装 PDF 解析器，未伪转换。")

    try:
        with tempfile.TemporaryDirectory(prefix="pangea-pdf-") as temporary:
            output = Path(temporary) / "document.txt"
            stderr = Path(temporary) / "pdftotext.stderr"
            environment = os.environ.copy()
            environment["PAGER"] = "cat"
            with stderr.open("wb") as error_file:
                result = subprocess.run(
                    [executable, "-layout", "-enc", "UTF-8", str(path), str(output)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=error_file,
                    check=False,
                    timeout=PDF_TIMEOUT_SECONDS,
                    env=environment,
                )
            error = _bounded_file(stderr)
            if result.returncode:
                return _pending(path, f"PDF：{path.name}", f"pdftotext 解析失败（exit {result.returncode}）：{error or '未提供错误信息'}；未伪转换。")
            if not output.exists():
                return _pending(path, f"PDF：{path.name}", "pdftotext 未生成文本输出；未伪转换。")
            if output.stat().st_size > MAX_PDF_TEXT_BYTES:
                return _pending(path, f"PDF：{path.name}", f"pdftotext 输出超过 {MAX_PDF_TEXT_BYTES} 字节边界；未导入不完整文本。")
            text = output.read_text(encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return _pending(path, f"PDF：{path.name}", f"pdftotext 在 {PDF_TIMEOUT_SECONDS} 秒内未完成；未伪转换。")
    except OSError as exc:
        return _pending(path, f"PDF：{path.name}", f"无法运行本地 pdftotext：{exc}；未伪转换。")

    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if not any(page.strip() for page in pages):
        return _pending(path, f"PDF：{path.name}", "pdftotext 未提取到可索引文本；可能是扫描件或受保护文件，未伪转换。")
    lines = [f"# {path.stem}", ""]
    for page, value in enumerate(pages, 1):
        lines.extend([f"<!-- page:{page} -->", value.strip(), ""])
    return ConversionResult(path, "\n".join(lines))


def _bounded_file(path: Path, limit: int = 4096) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    text = content[:limit].decode("utf-8", errors="replace").strip()
    return text + "\n... 错误输出已截断" if len(content) > limit else text


def _plain_text(path: Path) -> str:
    lines = [f"# {path.stem}", ""]
    for number, value in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        lines.extend([f"<!-- source:{path.name} line:{number} -->", value, ""])
    return "\n".join(lines)


def _csv(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    lines = [f"# {path.stem}", "", f"<!-- source:{path.name} -->", ""]
    if not rows:
        return "\n".join(lines)
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    header = [cell.replace("|", "\\|") or f"列{index}" for index, cell in enumerate(normalized[0], 1)]
    lines.extend(["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"])
    for row_number, row in enumerate(normalized[1:], 2):
        lines.append(f"<!-- source:{path.name} row:{row_number} -->")
        lines.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", "<br>") for cell in row) + " |")
    return "\n".join(lines) + "\n"


def write_markdown(
    result: ConversionResult,
    destination: str | Path,
    *,
    managed_root: str | Path | None = None,
) -> Path:
    destination = Path(destination)
    root, destination = _output_context(destination, managed_root)
    _atomic_publish_bytes(destination, result.markdown.encode("utf-8"), root)
    return destination


def publish_file(source: str | Path, destination: str | Path, *, managed_root: str | Path) -> Path:
    source_path = Path(source)
    source_info = source_path.lstat()
    if not stat.S_ISREG(source_info.st_mode):
        raise OutputSecurityError(f"发布源不是普通文件: {source_path}")
    destination_path = Path(destination)
    root, destination_path = _output_context(destination_path, managed_root)
    _validate_output_target(destination_path, root)
    with source_path.open("rb") as handle:
        _atomic_publish_stream(destination_path, handle, root)
    return destination_path


def _output_context(path: Path, managed_root: str | Path | None) -> tuple[Path, Path]:
    root = Path(managed_root) if managed_root is not None else path.parent
    raw_root = root.absolute()
    raw_path = path.absolute()
    try:
        info = root.lstat()
    except FileNotFoundError as exc:
        raise OutputSecurityError(f"输出根目录不存在: {root}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise OutputSecurityError(f"输出根目录不是实际目录: {root}")
    resolved_root = root.resolve(strict=True)
    try:
        relative = raw_path.relative_to(raw_root)
    except ValueError as exc:
        raise OutputSecurityError(f"输出目标越界: {path}") from exc
    return resolved_root, resolved_root / relative


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _ensure_output_directory(path: Path, root: Path) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise OutputSecurityError(f"输出目录越界: {path}") from exc
    current = root
    for component in relative.parts:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise OutputSecurityError(f"输出路径包含非目录: {current}")
        resolved = current.resolve(strict=True)
        if not _is_within(resolved, root):
            raise OutputSecurityError(f"输出目录越界: {current}")
    return path.resolve(strict=True)


def _validate_output_target(destination: Path, root: Path) -> None:
    _ensure_output_directory(destination.parent, root)
    try:
        info = destination.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise OutputSecurityError(f"拒绝非普通输出目标: {destination}")
    resolved = destination.resolve(strict=True)
    if not _is_within(resolved, root):
        raise OutputSecurityError(f"输出目标越界: {destination}")


def _atomic_publish_stream(destination: Path, source, root: Path) -> None:
    _validate_output_target(destination, root)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=destination.parent, prefix=".convert-", delete=False) as handle:
            temporary = Path(handle.name)
            shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())
        if not stat.S_ISREG(temporary.lstat().st_mode):
            raise OutputSecurityError(f"转换临时输出不是普通文件: {temporary}")
        _validate_output_target(destination, root)
        os.replace(temporary, destination)
        temporary = None
        _validate_output_target(destination, root)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _atomic_publish_bytes(destination: Path, content: bytes, root: Path) -> None:
    with tempfile.TemporaryFile("w+b") as source:
        source.write(content)
        source.seek(0)
        _atomic_publish_stream(destination, source, root)


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        return ET.fromstring(archive.read(name))
    except KeyError:
        return None


def _text(node: ET.Element) -> str:
    return "".join(part.text or "" for part in node.iter() if _tag(part) == "t").strip()


def _tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _node_text(node: ET.Element) -> str:
    return "".join(node.itertext()).strip()


def _copy_media(
    archive: zipfile.ZipFile,
    prefix: str,
    assets_dir: Path | None,
    assets_root: Path | None,
) -> list[Path]:
    names = [name for name in archive.namelist() if name.startswith(prefix) and not name.endswith("/")]
    if not assets_dir or not names:
        return []
    if assets_root is None:
        raise OutputSecurityError("资产输出缺少受管根目录")
    _ensure_output_directory(assets_dir, assets_root)
    assets: list[Path] = []
    for name in names:
        target = assets_dir / Path(name).name
        with archive.open(name) as src:
            _atomic_publish_stream(target, src, assets_root)
        assets.append(target)
    return assets


def _media_names(archive: zipfile.ZipFile, prefix: str) -> list[str]:
    return [name for name in archive.namelist() if name.startswith(prefix) and not name.endswith("/")]


def _visual_evidence_lines(media_names: list[str], assets: list[Path], locations: dict[str, str] | None = None) -> list[str]:
    if not media_names:
        return []
    extracted = {path.name for path in assets}
    lines = ["", "### 未解析视觉证据"]
    for name in media_names:
        filename = Path(name).name
        location = (locations or {}).get(filename, "全局未定位")
        label = f"未解析视觉证据: {location} / {filename}"
        if filename in extracted:
            lines.append(f"- [{label}](assets/{filename})")
        else:
            lines.append(f"- [{label}（未提取）]")
    lines.append("")
    return lines


def _page_breaks(node: ET.Element) -> int:
    count = 0
    for child in node.iter():
        if _tag(child) == "lastRenderedPageBreak":
            count += 1
        elif _tag(child) == "br" and any(key.rsplit("}", 1)[-1] == "type" and value == "page" for key, value in child.attrib.items()):
            count += 1
    return count


def _relationship_targets(archive: zipfile.ZipFile, name: str) -> list[str]:
    root = _xml(archive, name)
    return [str(rel.attrib.get("Target")) for rel in list(root) if rel.attrib.get("Target")] if root is not None else []


def _resolve_zip(source_file: str, target: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_file), target))


def _docx(path: Path, assets_dir: Path | None, assets_root: Path | None) -> tuple[str, list[Path]]:
    with zipfile.ZipFile(path) as archive:
        document = _xml(archive, "word/document.xml")
        if document is None:
            raise ConversionError(f"DOCX 缺少 document.xml: {path}")
        lines = [f"# {path.stem}", ""]
        paragraph = 0
        has_page_markers = _page_breaks(document) > 0
        page = 1
        for child in document.iter():
            if _tag(child) == "p":
                value = _text(child)
                if value:
                    paragraph += 1
                    page_anchor = str(page) if has_page_markers else "unknown"
                    lines.extend([f"<!-- page:{page_anchor} paragraph:{paragraph} -->", value, ""])
                page += _page_breaks(child)
            elif _tag(child) == "tbl":
                rows = []
                for row in child:
                    cells = [_text(cell).replace("|", "\\|") for cell in row if _tag(cell) == "tc"]
                    if cells:
                        rows.append(cells)
                if rows:
                    lines.append("| " + " | ".join(rows[0]) + " |")
                    lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
                    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
                    lines.append("")
        media_names = _media_names(archive, "word/media/")
        assets = _copy_media(archive, "word/media/", assets_dir, assets_root)
    lines += _visual_evidence_lines(media_names, assets)
    return "\n".join(lines), assets


def _xlsx(path: Path, assets_dir: Path | None, assets_root: Path | None) -> tuple[str, list[Path]]:
    with zipfile.ZipFile(path) as archive:
        shared_root = _xml(archive, "xl/sharedStrings.xml")
        shared = [_text(node) for node in shared_root if _tag(node) == "si"] if shared_root is not None else []
        workbook = _xml(archive, "xl/workbook.xml")
        rels = _xml(archive, "xl/_rels/workbook.xml.rels")
        targets = {rel.attrib.get("Id"): rel.attrib.get("Target", "") for rel in rels or []}
        lines = [f"# {path.stem}", ""]
        image_locations: dict[str, str] = {}
        for sheet in workbook.iter() if workbook is not None else []:
            if _tag(sheet) != "sheet":
                continue
            name, rel_id = sheet.attrib.get("name", "Sheet"), sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = targets.get(rel_id, "")
            if not target:
                continue
            xml_name = "xl/" + target.lstrip("/")
            sheet_rels = posixpath.join(posixpath.dirname(xml_name), "_rels", posixpath.basename(xml_name) + ".rels")
            for related in _relationship_targets(archive, sheet_rels):
                related_path = _resolve_zip(xml_name, related)
                if related_path.startswith("xl/media/"):
                    image_locations[Path(related_path).name] = f"Sheet {name}"
                if related_path.startswith("xl/drawings/"):
                    drawing_rels = posixpath.join(posixpath.dirname(related_path), "_rels", posixpath.basename(related_path) + ".rels")
                    for drawing_target in _relationship_targets(archive, drawing_rels):
                        media_path = _resolve_zip(related_path, drawing_target)
                        if media_path.startswith("xl/media/"):
                            image_locations[Path(media_path).name] = f"Sheet {name}"
            root = _xml(archive, xml_name)
            lines.extend([f"## Sheet: {name}", f"<!-- sheet:{name} -->", ""])
            for cell in root.iter() if root is not None else []:
                if _tag(cell) != "c":
                    continue
                ref, typ = cell.attrib.get("r", "?"), cell.attrib.get("t")
                value_node = next((item for item in cell if _tag(item) in {"v", "is"}), None)
                value = _node_text(value_node) if value_node is not None else ""
                if typ == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
                lines.append(f"- `{name}!{ref}`: {value}")
            lines.append("")
        media_names = _media_names(archive, "xl/media/")
        assets = _copy_media(archive, "xl/media/", assets_dir, assets_root)
    lines += _visual_evidence_lines(media_names, assets, image_locations)
    return "\n".join(lines), assets


def _pptx(path: Path, assets_dir: Path | None, assets_root: Path | None) -> tuple[str, list[Path]]:
    with zipfile.ZipFile(path) as archive:
        slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.search(r"\d+", n).group()))
        lines = [f"# {path.stem}", ""]
        image_locations: dict[str, str] = {}
        for index, name in enumerate(slides, 1):
            root = _xml(archive, name)
            values = [_text(node) for node in root.iter() if _tag(node) == "p" and _text(node)] if root is not None else []
            lines.extend([f"## 幻灯片 {index}", f"<!-- slide:{index} -->", *values, ""])
            rel_name = posixpath.join(posixpath.dirname(name), "_rels", posixpath.basename(name) + ".rels")
            for target in _relationship_targets(archive, rel_name):
                media_path = _resolve_zip(name, target)
                if media_path.startswith("ppt/media/"):
                    image_locations[Path(media_path).name] = f"幻灯片 {index}"
        media_names = _media_names(archive, "ppt/media/")
        assets = _copy_media(archive, "ppt/media/", assets_dir, assets_root)
    lines += _visual_evidence_lines(media_names, assets, image_locations)
    return "\n".join(lines), assets
