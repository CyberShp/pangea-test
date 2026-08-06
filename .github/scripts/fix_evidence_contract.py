from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / ".opencode/agents/pangea-test.md"
text = path.read_text(encoding="utf-8")
old = "运行时验证仓库、commit、snapshot、相对路径、文件 SHA、行范围摘要和可选 symbol。"
new = "运行时验证仓库、commit、snapshot、相对路径、`file_sha256`、`excerpt_sha256`、精确行范围和可选 symbol。"
if text.count(old) != 1:
    raise SystemExit(f"evidence policy count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
