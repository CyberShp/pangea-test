---
description: 从 manifest 恢复一个未完成的 PANGEA 深度任务
agent: dispatcher
---

恢复任务：`$ARGUMENTS`

必须执行：
1. 参数必须指向 `runs/<任务id>`；不要根据自然语言猜测其他目录。
2. 执行 `python runtime/runctl.py resume --run-dir <目录>` 获取 `next_tasks`。
3. 只派发 `pending`、`partial` 或 `failed` 的任务；`complete` 必须跳过。
4. `partial` 任务应把原证据中的 `progress.resume_hint` 一并传给能力 subagent。
5. 新结果经 `put-artifact` 校验入库后，重新汇总并调用 auditor。
6. 达到 `audit.max_rounds` 时停止自动回挖，把未决项交给用户裁决。
