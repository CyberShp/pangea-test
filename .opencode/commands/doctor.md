---
description: 诊断 PANGEA-TEST 的直接专家模式与托管任务模式环境
agent: pangea-test
---

执行 `python runtime/doctor.py`，原样读取 JSON 结果并用表格展示：检查项、状态、适用范围、详情。

必须分别给出：
- 直接专家模式是否可用；
- 托管任务模式是否可用；
- WARN 是否只是可选增强；
- `opencode_runtime_discovery=MANUAL` 不得伪称已自动验证，提示用户观察 Tab 列表。
