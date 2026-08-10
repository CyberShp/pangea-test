# PANGEA-TEST Agent Rules

## 运行条件门禁

- 任务开始前先检查关键运行条件是否满足；若不满足，只进行一轮检查和一轮最小尝试。仍未解决则立即停止，不开展替代工作，向用户报告阻塞并请求所需信息或输入。
- Preflight 中与当前任务无关的仓库 blocked、可选工具缺失只记录为降级信息；不安装、不排障、不重试。只有当前目标仓库不可读或正式流程必需能力缺失时才阻塞任务。

## 禁止触发网络安全类请求

- 本项目的开发、测试和审计不得发起、模拟或扩展任何网络安全类请求；不得执行渗透测试、漏洞利用、扫描、攻击链或外部目标探测。
- 本地文件发布、并发修改、事务恢复、路径和权限检查一律定义为“软件事务一致性 QA”或“数据完整性测试”，只描述输入时序、状态转换、期望结果和恢复结果，不使用攻击、利用、绕过、对抗、恶意篡改等安全化叙事。
- 相关测试只能作用于本工作区、冻结的只读源码快照和系统临时目录；不得因此访问网络、真实设备、外部账号或工作区外的非临时数据。
- 一旦某个 Agent 或请求触发内容分类警告，必须立即停止并中断该线程；禁止在同一线程中重试、改写提示后重试、自动续跑或将其复用于其他审计。
- 后续确需继续同一软件 QA 工作时，必须由主 Agent 创建全新子 Agent，使用 `fork_turns: "none"` 和最小化的中性任务说明，不得继承此前对话。
- 实现子 Agent 不得自行创建、唤起、复用或转交审计 Agent；独立审计只由主 Agent统一派发。
- 如无法在上述边界内完成验证，应将该项记为未验证并报告主 Agent，不得扩大请求范围。

## 模块分析前置规则

- 模块全量分析在生成任务契约前，必须使用 `<python> -m tooling.pangea_cli repo locate ...` 直接定位模块；`locate` 不是 `runctl.py` 子命令。禁止尝试 `runctl.py locate`，也禁止在 locate 有结果后继续 list/glob/Python walk 重复找目录。只有 locate 无结果时允许一次补充搜索。
- 模块路径确定后执行一次 `repo related`，只将 include、registration、shared_symbol 作为跨模块候选；存在候选时先让用户决定是否纳入，再生成任务契约。
- 模块范围确定后不得由主 Agent 手工 read/grep 建立另一套代码地图，也不得因为只读了部分核心文件而提前得出“已了解核心逻辑”的结论。契约激活后直接进入 `prepare-semantic-analysis-v2`，由 Runtime 对确认 scope 完整读取并建立 code map。只有定位候选无法区分或 Runtime 明确报告证据缺口时，才允许定点补读。
- `/module-analysis` 的 runctl 命令必须按正式命令模板执行，不得自行增加模板未声明参数。`draft-contract-v2` 必须包含 `--scenario module-analysis`、`--target`、`--repository` 和确认后的 `--source-scope`；禁止传 `--repository-commit`、`--capability-pack`。参数未知的非正式诊断命令才允许先查 `--help`。
- `--source-scope` 每个路径独立传入，格式固定为 `<仓名>=<规范相对路径>`，禁止逗号拼接多个路径。
- `revise-contract-v2 --file` 必须传修改后的 `task_contract` 对象本身，不得传外层 contract record。以 draft 返回的 `task_contract` 为基准修改，再配合当前 `contract_id` 和 `expected_revision` 调用 revise。
- 模块范围和 complete 契约确认统一使用 question 工具。范围确认固定为“仅当前模块 / 纳入建议关联模块 / 自定义范围”；complete 最终确认固定为“按当前范围开始 / 补充材料 / 调整范围”。不要在不同 Run 中随意改成交互形式不同的自由提问。

## 工作区保护

- `.codebuddy/` 属于用户文件，禁止读取、修改、删除或纳入提交。
- 保留用户已有改动；不得用破坏性 Git 或文件命令清理工作树。
- 正式 Run 执行期间禁止在项目根目录或 `pangea-data/` 下创建临时调试/修复脚本；内置诊断不足时报告能力缺口，不得自行写 `diag_*.py`、`fix_*.py` 等维修脚本继续 Run。
- `pangea-data/runs/*/internal/semantic-analysis/plan.json`、`units/*.json` 等 Runtime 内部工件只能通过 PANGEA 正式 CLI 写入、恢复或重置；禁止直接编辑或用临时脚本批量改写。
- 只有用户明确要求“开发/修复 PANGEA-TEST 本身”时才进入独立开发任务；开发任务不得同时继续当前分析 Run。
