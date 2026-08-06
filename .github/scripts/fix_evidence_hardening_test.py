from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / "tests/test_evidence_provenance.py"
text = path.read_text(encoding="utf-8")
old = '"excerpt_sha256": hashlib.sha256(excerpt).hexdigest(), "claim": "额外材料内容"'
new = '"excerpt_sha256": hashlib.sha256(excerpt).hexdigest(), "claim": "契约外额外材料内容不得被消费"'
if text.count(old) != 1:
    raise SystemExit(f"material claim fixture count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
