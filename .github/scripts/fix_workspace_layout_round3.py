from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:200]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/test_repository_runtime.py",
    '''                with self.assertRaisesRegex(repository_runtime.RepositoryRuntimeError, "Run 固定目录 tmp"):
''',
    '''                with self.assertRaisesRegex(repository_runtime.RepositoryRuntimeError, "Run 可选目录 tmp"):
''',
)

replace_once(
    ".opencode/commands/initial.md",
    '''`data session-prepare` 的输出已经包含 `incomplete_runs`：直接呈现该字段，不再重复运行 `data incomplete-runs`。`index all` 只为受管影子仓建立或更新 GitNexus 索引；其耗时、磁盘占用、增量能力、工具缺失和单仓失败均以实际输出为准。
''',
    '''`data session-prepare` 的输出已经包含 `incomplete_runs`：直接呈现该字段，不再重复运行 `data incomplete-runs`。同时必须读取并呈现 `workspace_inventory`：`formal_reports` 是用户正式交付，`run_history` 是历史 Run 与中间记录，`legacy_reports` 是旧版 Run 内报告。不得把 `internal/report-model.json`、checkpoint、audit JSON 或聊天摘要当成正式报告。`index all` 只为受管影子仓建立或更新 GitNexus 索引；其耗时、磁盘占用、增量能力、工具缺失和单仓失败均以实际输出为准。
''',
)
replace_once(
    ".opencode/commands/initial.md",
    '''5. 输出能力清单、工具缺口、仓库状态、索引结果和未完成 Run；没有实际数据时明确说明，不猜测。
''',
    '''5. 输出能力清单、工具缺口、仓库状态、索引结果、未完成 Run、正式报告路径和旧版迁移缺口；没有实际数据时明确说明，不猜测。
''',
)

architecture = ROOT / "docs/architecture.md"
text = architecture.read_text(encoding="utf-8")
text = re.sub(
    r'''```text\npangea-data/\n  inbox/\n  library/\{sources,markdown,assets,catalog\.jsonl\}\n  repositories/\n  indexes/\n  runs/<run-id>/\{manifest\.json,checkpoints,evidence,internal,tmp,final\}\n  registry/\n```''',
    '''```text
pangea-data/
  inbox/                         # 用户原始资料
  repositories/                  # 已登记只读仓库
  library/{sources,markdown,assets,catalog.jsonl}  # 有资料后按需创建
  indexes/{records,shadows}      # 有索引任务后按需创建
  runs/<run-id>/                 # 历史 Run 与中间工件
  reports/<run-id>/{report.md,report.html}         # 唯一正式交付
```''',
    text,
    count=1,
)
text = text.replace(
    '''主 Agent 在审计前，将完整报告模型写入唯一被审文件 `runs/<run-id>/internal/report-model.json`，并计算 SHA-256。''',
    '''主 Agent 在审计前必须调用 `stage-report-v2`，由确定性运行时将完整报告模型原子写入唯一被审文件 `runs/<run-id>/internal/report-model.json`，并返回 SHA-256。''',
)
text = text.replace(
    '''只有固定模型绑定仍一致的 PASS 意见，才能通过 `finalize-v2 --model <run-dir>/internal/report-model.json` 生成 `report.md` 和 `report.html`；PASS 后模型改变必须重新审计。''',
    '''只有固定模型绑定仍一致的 PASS 意见，才能通过 `finalize-v2 --model <run-dir>/internal/report-model.json` 在 `pangea-data/reports/<run-id>/` 生成正式 `report.md` 和 `report.html`；两个文件实际存在且非空后才算完成。PASS 后模型改变必须重新审计。''',
)
text = text.replace(
    '''`final/` 输出内容一致的 `report.md` 和完全离线单文件 `report.html`。''',
    '''`pangea-data/reports/<run-id>/` 输出内容一致的 `report.md` 和完全离线单文件 `report.html`；Run 目录只保留历史记录和中间工件。''',
)
text = text.replace(
    '''可保留历史实现文件作为迁移参考，但不得从活动 CLI 路径触发旧 `workspace/outputs + runctl init` 协议。''',
    '''旧根目录 `source/inputs/workspace/outputs/projects/runs` 已从活动仓库移除；本地遗留内容只检测、不自动移动或删除，也不得从活动 CLI 路径触发旧 `workspace/outputs + runctl init` 协议。''',
)
architecture.write_text(text, encoding="utf-8")
