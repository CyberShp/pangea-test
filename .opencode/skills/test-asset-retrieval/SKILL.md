---
name: test-asset-retrieval
description: 按项目 profile、资产类型、标签和任务关键词检索测试团队长期资产库
---

# Test Asset Retrieval

正式分析前：

1. 读取当前项目的 `asset_profiles`。
2. 调用 `python -m tooling.pangea_cli asset search`，按 profile/type/tag/query 检索。
3. 优先使用 `status=approved` 资产；禁止全量读取 `assets/`。
4. 历史经验与故障模式只提供补漏线索，不能替代源码、设计和运行证据。
5. 实际消费的 `asset_id` 必须写入本次 `inputs.lock.json`。
6. 已有用例用于去重、回归范围和覆盖补漏，不得无判断复制为新用例。
