from pathlib import Path

root = Path(__file__).resolve().parents[2]

initial_path = root / ".opencode/commands/initial.md"
initial = initial_path.read_text(encoding="utf-8")
classification = '''

## 新增资料的增量语义分类

portable preflight 完成后，读取 `step_results.session_prepare` 的 `inbox.added`、`inbox.changed` 和 `catalog`。只有新增与变化数量大于零时才进入分类；两者都为 `0` 时不得读取全部 Markdown 或重分类。

1. 从 catalog 关联本次新增或变化记录，只处理存在 `markdown_path`、转换可读且没有有效 `semantic_classification` 的项目。`classification_sha256` 与当前 SHA-256 一致的既有分类跳过；同哈希继承分类也跳过。
2. 先读取标题、目录、转换锚点和必要锚点，只有分类判断需要时才展开相关段落。多个候选可以由子 Agent 并行读取。
3. 分类必须包含 role、tags、summary、applicable_modules、versions、confidence、rationale，并显式写入 `"source_backed": false` 与 `"provenance": "model_inference"`。这些字段属于资料整理推断，不是材料事实；正式分析仍回到 Markdown 来源锚点。
4. 分类结果准备后按 source_path 逐条串行执行，禁止并发写 catalog：

```text
<preflight.python_executable> -m tooling.pangea_cli library classify --source-path "<catalog.source_path>" --file <classification.json>
```

只报告实际写入成功的分类；失败项保留为未分类。
'''
if "## 新增资料的增量语义分类" not in initial:
    initial += classification
initial_path.write_text(initial, encoding="utf-8")

path = root / "tests/test_agent_v2.py"
text = path.read_text(encoding="utf-8")
old = '''        self.assertIn("tooling.pangea_cli preflight", combined)
        self.assertNotIn("cd /d", combined.lower())
        self.assertNotIn("&&", combined)
        self.assertNotIn("python3 runtime/runctl.py", combined)
'''
new = '''        self.assertIn("tooling.pangea_cli preflight", combined)
        self.assertIn("禁止 `cd`", combined)
        self.assertIn("不得使用 `&&`", combined)
        self.assertNotIn("python3 runtime/runctl.py", combined)
'''
if text.count(old) != 1:
    raise SystemExit(f"portable test block count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
