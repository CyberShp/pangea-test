# Mini storage module fixture

这是 PANGEA-TEST 的稳定 Smoke 分析对象，不代表生产代码。

预期可被识别的结构与风险：

- `connection_open`：外部入口、资源申请、LIVE 状态建立及日志观测点。
- `connection_handle_event`：LIVE → RECOVERING → LIVE/CLOSED 状态转换。
- `connection_recover`：最多 3 次重试与 ETIMEDOUT 终止路径。
- `allocate_request_buffer`：`inject_failure=true` 时故意泄漏已分配缓冲区。
- `submit_request`：transport error 路径故意遗漏 `inflight--`，形成计数累积风险。
- 错误通过返回码和 stderr 日志向外传播，可转换为黑盒观测判据。

Smoke 的目标是验证编排链路稳定，不要求每次生成完全相同的自然语言报告。
