---
description: 对仓内 mini-storage-module 执行一次可重复的端到端托管 Smoke
agent: dispatcher
---

执行 PANGEA-TEST 托管链路 Smoke：

1. 先运行 `python runtime/doctor.py`；托管任务模式不可用则停止并报告失败项。
2. 创建唯一的新 run：`python runtime/managed.py smoke-init --max-parallel 3`。
3. 读取该 run 的 task envelope、manifest，以及 `tests/fixtures/mini-storage-module/README.md`。
4. 调用 `dev-expert`，仅派发 manifest 登记任务。证据先保存为 JSON，再经 `python runtime/managed.py put-artifact` 校验并入库。
5. 汇总后调用 auditor，并经 `python runtime/runctl.py apply-audit` 入库。
6. 若 FAIL/CONCERNS，运行 `python runtime/managed.py plan-rework --run-dir <目录>`，只派发其 `next_tasks`；展示 `manual_actions`。
7. 最终报告 Smoke 链路各阶段是否成功，并列出 fixture 中预期风险是否被发现；不得用预期答案伪造源码证据。
