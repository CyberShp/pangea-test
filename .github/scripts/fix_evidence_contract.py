from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / ".opencode/agents/pangea-test.md"
text = path.read_text(encoding="utf-8")
replacements = [
    (
        "运行时验证仓库、commit、snapshot、相对路径、文件 SHA、行范围摘要和可选 symbol。",
        "运行时验证仓库、commit、snapshot、相对路径、`file_sha256`、`excerpt_sha256`、精确行范围和可选 symbol。",
        "evidence policy",
    ),
    (
        "该工件是材料选择、搜索广度、MR facts 和源码行证据的唯一真实性来源。",
        "该工件是材料选择、搜索广度、`mr_facts` 和源码行证据的唯一真实性来源。",
        "mr facts policy",
    ),
]
for old, new, label in replacements:
    if text.count(old) != 1:
        raise SystemExit(f"{label} count={text.count(old)}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
