---
description: 启动模块全量分析，创建可恢复 run 并交给 dev-expert 执行
agent: dispatcher
---

启动 PANGEA-TEST 的“模块全量分析”。

用户参数：`$ARGUMENTS`

必须执行：
1. 从参数中取得分析对象与源码路径；缺失时只补问缺失项。
2. 调用 `python runtime/runctl.py init --scenario module-full-analysis --target <对象> --source-path <源码路径>` 创建任务。该命令默认只依赖 Python 标准库，不要求 pip 安装。
3. 读取生成的 `task-envelope.json` 和 `manifest.json`。
4. 用 Task 调用 `dev-expert`，传入完整 task envelope；不要自行改写字段。
5. dev-expert 返回证据后，必须先通过 `runctl.py put-artifact` 校验并入库，再进入汇总。
6. 未通过 auditor 前，不得宣称任务完成。
