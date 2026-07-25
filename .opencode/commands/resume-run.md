---
description: 从 manifest 恢复未完成的托管任务
agent: dispatcher
---

参数：`$ARGUMENTS`

1. 参数必须指向 `runs/<任务id>`。
2. 执行 `python runtime/runctl.py resume --run-dir <目录>`。
3. 只派发 `next_tasks`；complete 必须跳过，partial 必须携带原 `resume_hint`。
4. 新结果经 `python runtime/managed.py put-artifact` 后重新汇总和审计。
5. FAIL/CONCERNS 先执行 `python runtime/managed.py plan-rework --run-dir <目录>`，只派发其 `next_tasks`，展示 `manual_actions`。
