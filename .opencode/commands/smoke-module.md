---
description: 对仓内 mini-storage-module 执行一次可重复的端到端托管 Smoke
agent: dispatcher
---

执行 PANGEA-TEST 托管链路 Smoke：

1. 先运行 `python runtime/doctor.py`；托管任务模式不可用则停止并报告失败项。
2. 创建新 run：
   `python runtime/runctl.py init --scenario module-full-analysis --target mini-storage-module --source-path tests/fixtures/mini-storage-module --task-id smoke-module --new-run --max-parallel 3`
3. 读取该 run 的 task envelope、manifest，以及 `tests/fixtures/mini-storage-module/README.md`。
4. 调用 `dev-expert`，仅派发 manifest 登记任务。证据必须经 `put-artifact` 入库。
5. 汇总后调用 auditor，并经 `apply-audit` 入库。
6. 若 FAIL/CONCERNS，运行 `plan-rework`，只派发其 `next_tasks`；展示 `manual_actions`。
7. 最终报告 Smoke 链路各阶段是否成功，并列出 fixture 中预期风险是否被发现；不得用预期答案伪造源码证据。
