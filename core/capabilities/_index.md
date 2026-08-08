# PANGEA-TEST capability packs

本目录定义可组合分析材料，不定义人设或 runtime task。用户只面对 `pangea-test`；运行时把适用包和 Storage Skill receipt 注入同一个 `analysis-worker` 的 obligation context pack。

## 共享底座

| 能力 | 文件 | 用途 |
|---|---|---|
| C/C++ 源码取证 | [shared-cpp-evidence.md](shared-cpp-evidence.md) | 入口、调用链、状态、资源、错误和并发证据 |
| 测试语义转译 | [test-semantic-translation.md](test-semantic-translation.md) | 将因果链转成黑盒 control/oracle |
| 风险卡契约 | [risk-card-contract.md](risk-card-contract.md) | 约束 fragment 内可合并风险 |
| 条件知识加载 | [conditional-knowledge.md](conditional-knowledge.md) | 记录证据门控的外部知识 |

## 六个能力包

| ID | 文件 | 主要分析面 |
|---|---|---|
| `functional-state` | [功能与状态.md](dfx/功能与状态.md) | 外部入口、状态机、功能边界和分支 |
| `resource-specification` | [资源与规格.md](dfx/资源与规格.md) | 所有权、配额、队列、申请释放和泄漏 |
| `performance-pressure` | [性能与压力.md](dfx/性能与压力.md) | 吞吐、时延、压力、争用和恢复 |
| `concurrency-exception` | [并发与异常.md](dfx/并发与异常.md) | 共享状态、异步、竞态、超时和错误 |
| `upgrade-compatibility` | [升级与兼容.md](dfx/升级与兼容.md) | 版本、配置、持久状态和回滚 |
| `reliability-consistency` | [可靠性与一致性.md](dfx/可靠性与一致性.md) | 故障注入、恢复、一致性和可用性 |

## 组装规则

1. 每个 fragment 只使用运行时随 context pack 明示的材料；receipt 要绑定触发 obligation、版本和内容哈希。
2. 完整模块分析为六包各建立可审计 disposition；MR 仅加载证据命中的包。
3. capability pack 不能改写 obligation、创建新 task 或产生最终报告；worker 只提交 strict fragment，primary 合并，auditor 独立审阅。
