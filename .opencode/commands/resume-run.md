---
description: 从 project/workflow/run 工作区恢复未完成的托管任务
agent: pangea-test
---

参数：`$ARGUMENTS`

1. 参数必须指向 `workspace/<project>/<workflow>/<run>`，不要猜测其他目录。
2. 执行 `python runtime/runctl.py resume --run-dir <目录>`。
3. 只派发 `next_tasks`；complete 跳过，partial 携带 `resume_hint`。
4. 新结果经 `managed.py put-artifact` 后重新汇总和审计。
5. 完成后执行 `python -m tooling.pangea_cli workflow publish --run-dir <目录>`。
