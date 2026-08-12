# PANGEA v1

PANGEA v1 使用 OpenCode 对已登记仓库中的 C/C++ 模块做证据驱动分析，生成中文代码分析、
模块级六维 DFX、SFMEA、风险、黑盒/灰盒测试设计和 Markdown/HTML 报告。

产品规则只有一份：[PANGEA.md](PANGEA.md)。

## 环境

```text
python3 -m pip install -r runtime/requirements-strict.txt
opencode --version
```

Runtime 通过隔离配置调用 `analysis-worker`、`composer`、`dfx-analyst`、`risk-adjudicator`、`risk-analyst`、`test-designer` 和
`reviewer`。默认模型为 `deepseek/deepseek-v4-flash`，可用 `--model` 显式覆盖。

## 运行模块分析

```text
python3 runtime/runctl.py analyze \
  --repository spdk \
  --scope app/iscsi_tgt \
  --target "SPDK app/iscsi_tgt"
```

也可以从 OpenCode 使用：

```text
/analyze --repository spdk --scope app/iscsi_tgt --target "SPDK app/iscsi_tgt"
```

查看、恢复和显式重试：

```text
python3 runtime/runctl.py status --run-id <run-id>
python3 runtime/runctl.py resume --run-id <run-id>
python3 runtime/runctl.py retry --run-id <run-id>
```

对应的 OpenCode 命令为 `/status --run-id <run-id>`、`/resume --run-id <run-id>` 和
`/retry --run-id <run-id>`；每个命令只允许调用一次对应的 Runtime 子命令。

Slice worker 只生成语义 fragment；模块合成后才执行一次 DFX，再判定风险并派生 SFMEA 与测试。
测不了但证据成立的风险以 `testability=blocked` 保留。正式工件位于 `pangea-data/runs/<run-id>/`，完成报告位于
`pangea-data/reports/<run-id>/`。只有 `run.json.state` 为 `completed` 才表示交付完成。
