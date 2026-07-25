---
description: 启动模块全量分析托管任务
agent: dispatcher
---

用户参数：`$ARGUMENTS`

1. 取得分析对象与源码路径；缺失时只补问缺失项。
2. 执行 `python runtime/runctl.py init --scenario module-full-analysis --target <对象> --source-path <源码路径>`。
3. 读取完整 task envelope 和 manifest，调用 `dev-expert`。
4. 所有证据先保存 JSON，再经 `python runtime/managed.py put-artifact`；审计经 `python runtime/runctl.py apply-audit`。
5. FAIL/CONCERNS 必须经 `python runtime/managed.py plan-rework --run-dir <目录>` 生成受控任务；只派发 `next_tasks`，展示 `manual_actions`。
6. 达到审计轮数上限后带未决项结束，不得宣称全量通过。
