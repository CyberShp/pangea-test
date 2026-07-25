---
description: 显式启动模块全量分析（高级/调试入口，普通用户可直接自然语言表达）
agent: dispatcher
---

用户参数：`$ARGUMENTS`

1. 加载 `project-workspace` Skill。
2. 读取当前项目；未指定路径时从 project.json 自动取得源码。
3. 调用：`python -m tooling.pangea_cli workflow start --workflow-id module-full-analysis --target <对象>`。
4. 使用返回的 `run_dir/source_path/output_dir` 执行托管分析。
5. 所有证据经 `managed.py put-artifact`，审计和回挖遵循 PR #3 的托管闭环。
6. 完成后执行 `python -m tooling.pangea_cli workflow publish --run-dir <run_dir>`。
