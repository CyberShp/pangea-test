# PANGEA-TEST

PANGEA-TEST 是面向平台驱动测试团队的个人测试 Agent。它读取 MR、diff、提交记录、设计资料和本地只读代码仓，追踪入口、调用链、状态变化、异常分支与资源生命周期，最终把源码风险翻译成测试人员可执行的黑盒测试场景、少量灰盒观测或插桩需求。

用户只需要面对一个 `pangea-test`，不需要选择所谓的“性能专家”“并发专家”或“资源专家”。六个 DFX 子 Agent 由主 Agent 在内部按场景调度、并发分析和统一汇总。

当前正式支持两类任务：

- `/mr-regression`：针对一个 MR 生成回归范围、风险与测试建议。
- `/module-analysis`：针对一个功能模块生成全量测试分析和用例。

补丁测试策略和真实用例执行是后续能力，当前版本不会登录复杂主机阵列组网代替测试人员执行用例。

## 目录

- [10 分钟上手](#10-分钟上手)
- [运行环境](#运行环境)
- [准备代码仓和测试资料](#准备代码仓和测试资料)
- [首次初始化](#首次初始化)
- [MR 回归测试建议](#mr-回归测试建议)
- [模块全量测试分析](#模块全量测试分析)
- [如何向 Agent 描述任务](#如何向-agent-描述任务)
- [执行状态与内部分析](#执行状态与内部分析)
- [报告与用例](#报告与用例)
- [断点恢复](#断点恢复)
- [黑盒、灰盒和插桩边界](#黑盒灰盒和插桩边界)
- [文件和 Run 管理](#文件和-run-管理)
- [安全边界](#安全边界)
- [常见问题](#常见问题)
- [维护与诊断命令](#维护与诊断命令)

## Windows 与 PowerShell

在 Windows 上不要使用 `cd /d/... && python3 ...`。PANGEA 正式入口使用一个进程完成根目录验证和初始化：

```powershell
python -m tooling.pangea_cli preflight
```

从项目目录启动 OpenCode 后直接运行 `/initial`，Agent 不应自行切换目录。路径包含空格或中文时无需转换；项目根目录只通过当前目录/父目录标记、显式 `--root` 或 `PANGEA_ROOT` 解析。若返回 `workspace_unresolved`，系统不会创建 `pangea-data`，也不会扫描其他盘符。

## 10 分钟上手

### 1. 获取项目

```bash
git clone https://github.com/CyberShp/pangea-test.git
cd pangea-test
```

### 2. 检查运行环境

```bash
python3 runtime/doctor.py
```

至少确认以下项目为 `PASS`：

- `repository_root`
- `primary_agent_identity`
- `six_hidden_dfx_agents`
- `v2_workflow_entrypoints`
- `python`
- `runctl`
- `pangea_cli`

GitNexus、`pdftotext`、clang-tidy、cppcheck、Semgrep 等属于可选能力。它们缺失时 Agent 会记录降级路径，不会假装已经使用。

### 3. 放入待分析代码仓

```bash
mkdir -p pangea-data/repositories
git clone <代码仓地址> pangea-data/repositories/driver
```

`driver` 是仓库登记名。后续告诉 Agent“仓库是 driver”即可，不要传任意绝对路径。

### 4. 放入可选资料

```bash
mkdir -p pangea-data/inbox
cp <需求或设计文档> pangea-data/inbox/
```

MR 回归通常不需要把大量需求和设计文档都塞进上下文。模块全量分析更适合使用存量用例、测试报告、历史缺陷和团队 Wiki 导出件。

### 5. 启动客户端并选择 Agent

从本项目目录启动已安装的 OpenCode，例如：

```bash
opencode .
```

通过 Agent 切换入口选择 `pangea-test`。在支持项目级 Agent 配置的 CodeAgent 中也必须明确切到 `pangea-test`；如果列表中没有它，说明客户端没有加载本项目根目录的 `.opencode/` 配置，此时普通默认 Agent 不具备 PANGEA-TEST 的工作流和门禁。

### 6. 初始化并开始任务

```text
/initial
```

然后选择一项：

```text
/mr-regression https://git.example.com/storage/driver/-/merge_requests/123
```

```text
/module-analysis iSCSI 连接与会话管理，仓库 driver，重点关注资源规格和异常恢复
```

用户也可以直接用自然语言描述。只要出现明确的 MR 回归或模块全量分析意图，Agent 会进入同一套正式流程。

## 运行环境

### 必需条件

- Git。
- Python 3.9 或更高版本。
- 能加载项目级 Agent、Command、Skill 和子 Agent 的 OpenCode 或兼容客户端。
- 至少一个位于 `pangea-data/repositories/` 的本地 Git 工作树。

### MR 场景所需能力

推荐在运行载体中配置可读取 MR 链接的 MCP。它应至少返回：

- MR 描述和开发自验信息。
- diff。
- 源分支、目标分支和精确 commit SHA。
- 关联 MR 或关联仓线索。

MR MCP 不可用时，可以向 Agent 提供导出的 MR 描述、diff 文件和 40 位 commit SHA。缺少精确版本时，Agent 不应把当前工作区代码冒充 MR 版本。

### 可选工具

| 工具 | 用途 | 缺失时行为 |
| --- | --- | --- |
| GitNexus | 代码关系索引和调用链检索 | 使用普通源码搜索，降低检索效率 |
| `pdftotext` | PDF 文本提取 | PDF 标记为待转换材料 |
| clang-tidy | C/C++ 静态分析补充 | 不使用该证据源 |
| cppcheck | C/C++ 静态分析补充 | 不使用该证据源 |
| Semgrep | 规则检索补充 | 不使用该证据源 |
| `jsonschema` | 完整 Draft 2020-12 校验 | 使用项目内置 stdlib 校验器 |

启用严格 JSON Schema 校验：

```bash
python3 -m pip install -r runtime/requirements-strict.txt
```

`/setup-tools` 只探测能力并给出安装计划，不会自动联网安装，也不会启动容器：

```text
/setup-tools gitnexus cppcheck
```

## 准备代码仓和测试资料

### 代码仓约定

所有分析仓库放在：

```text
pangea-data/repositories/<仓库登记名>/
```

示例：

```text
pangea-data/repositories/driver/
pangea-data/repositories/firmware/
pangea-data/repositories/bmc/
```

要求：

- 每个登记名对应一个真实的 Git 工作树。
- 仓库目录不能是符号链接。
- 仓库不能只是另一个上层 Git 仓中的普通子目录。
- Agent 不会在代码仓中增、删、改、暂存或提交文件。
- new session 只会在仓库干净、分支正常且能够安全快进时执行 `git pull --ff-only`。
- 脏仓、detached HEAD、分叉、认证失败或冲突风险都会跳过更新并说明原因。

代码由用户自行 `git clone`、`git pull` 或从其他目录复制。不要让 Agent 在源仓上切分支、reset、stash 或解决冲突。

### 多仓场景

卡件驱动和微码等场景可能横跨两个仓。把两个仓都登记后，在任务中说明：

```text
仓库 driver 和 firmware，driver 是主仓；如果 MR 中能找到关联 firmware MR，请一起分析。
```

MR 工作流会为每个已登记关联仓建立当前 Run 专属的只读 commit 快照。关联仓不可用时，Agent 先完成可见仓分析，并把缺失仓、版本和影响范围写入覆盖缺口与下一步建议。

### 文档约定

把用户导出的文件放入：

```text
pangea-data/inbox/
```

支持和降级行为：

- `.md`、`.txt`、`.csv`：直接转换并保留来源锚点。
- `.docx`、`.xlsx`、`.pptx`：使用本地解析器提取正文、Sheet/单元格、幻灯片和媒体关系。
- `.pdf`：本地存在 `pdftotext` 时按页提取；扫描件、受保护文件或无解析器时标记为待转换。
- `.doc`、`.xls`、`.ppt`：旧二进制 Office 文件不会伪转换，标记为待处理。

原文件不会被移动、改名或覆盖。系统按内容 SHA-256 归档，重复内容复用转换结果；新增或变化的文档才会触发分类。

## 首次初始化

在 Agent 会话中执行：

```text
/initial
```

它会依次完成：

1. 创建或检查 `pangea-data/` 目录结构。
2. 扫描 `inbox/` 中新增和变化的资料。
3. 转换可读取文档并建立来源锚点。
4. 对新增资料做语义归类，已有分类和同哈希资料不会重复处理。
5. 检查已登记仓库，并在安全条件满足时尝试 `git pull --ff-only`。
6. 探测 GitNexus、文档转换器和可选静态分析工具。
7. 为受管 shadow clone 建立或更新 GitNexus 索引。
8. 列出未完成 Run，便于恢复。

每个 new session 第一次正式响应前，Agent 也会执行等价的增量准备。同一 session 内不会因为任务切换而无条件重复全量扫描。

首次索引可能较慢。后续是否增量取决于内网 GitNexus 版本和实际能力探测结果；Agent 必须报告真实结果，不能只根据版本号猜测。

## MR 回归测试建议

### 适用场景

- 开发提交 MR 后，需要判断回归范围。
- 原问题已有复现步骤，需要验证修复和影响链。
- diff 很小，但怀疑状态、资源、并发或恢复路径有扩散影响。
- MR 涉及两个关联代码仓。

### 推荐输入

最少提供：

- MR 链接。
- 目标功能模块。
- 本地仓库登记名。

有则提供：

- 原问题现象和复现步骤。
- 开发自验结果。
- 版本、组网和硬件信息。
- 本次特别关注或明确不测的范围。
- 存量用例、覆盖率或历史缺陷材料。

示例：

```text
/mr-regression https://git.example.com/storage/driver/-/merge_requests/123

目标模块：iSCSI 连接管理
本地仓库：driver
版本：V8R2C10
组网：双控阵列，2 台 Linux 主机，每主机 8 条 iSCSI 连接
原问题：连接压力超过内部 cmd 规格后，即使连接数回落，IOPS 仍无法恢复
重点：资源计数、过载回落、连接断开和进程重拉前的在线恢复
排除：本轮不做升级测试
```

只有 MR 链接也可以开始。Agent 会先读取 MR、diff、commit 和源码反推目标与风险，再把不确定内容标为“推断”或“待确认”，而不是反复要求用户补齐所有背景。

### 分析流程

MR 回归固定经过：

1. `code_map`：定位入口、模块边界和关键状态。
2. `impact_chain`：从改动点追踪调用、状态、资源和外部影响。
3. `mr_baseline`：固定分析四类回归。
4. `dfx_route`：按 diff 和源码信号选择相关 DFX 子 Agent。
5. `branches`：识别异常分支、恢复分支和进入条件。
6. `risk_ledger`：汇总全部风险并定级。
7. `sfmea`：内部完成失效模式分析。
8. `test_design`：生成黑盒优先的场景和用例。
9. `report`：独立审计通过后生成最终报告。

四类固定回归是：

- 原场景回归。
- 改动功能验证。
- 影响链回归。
- 异常与恢复验证。

MR 不会机械调用全部专项分析。资源、性能、并发、升级等维度只在 diff、commit、源码或用户重点出现相关信号时深入。

### 你会得到什么

- 改动入口和影响链。
- 原问题是否被修复的验证建议。
- 必须测、建议测和可不测范围。
- 按 `Low`、`Medium`、`High`、`Critical` 分级的完整风险账本。
- 异常路径如何进入、如何观测、如何恢复。
- 可执行的黑盒用例和必要的灰盒诊断或插桩需求。
- 关联仓缺失、工具缺失或证据不足的明确缺口。

## 模块全量测试分析

### 适用场景

- 新接手一个驱动、协议或卡件模块，需要建立全量测试视图。
- 存量用例较旧，希望从源码重新识别遗漏场景。
- 需要对版本整体的规格、性能、升级、并发和可靠性风险做覆盖盘点。
- 希望重点深挖内部资源规格、泄漏、过载回落和长稳问题。

一次只选择一个明确功能模块，不要把 BMC、BIOS、卡件、协议驱动和后端盘一次性混成一个任务。

### 完整型

```text
/module-analysis iSCSI 连接与会话管理

仓库：driver
版本：V8R2C10
组网：双控阵列，多主机多路径
重点：内部 cmd 规格、资源泄漏、过载回落、异常连接时序
资料：pangea-data/inbox/iscsi-cases.xlsx
```

完整型会展开：

- 代码地图。
- 关键业务流程。
- 异常分支和恢复路径。
- 六个 DFX 维度。
- 命中专项深挖。
- 内部 SFMEA。
- 测试场景、用例和覆盖映射。

### 快速型

```text
/module-analysis iSCSI 连接与会话管理 --fast
```

快速型不删除阶段，也不省略六个 DFX 维度。它主要减少：

- 调用链展开层数。
- 非核心流程的分支枚举。
- 低信号证据的深挖程度。
- 专项分析中的旁路路径数量。

适合首次摸底和时间受限场景。报告会明确写出深度边界，不能把快速型结论伪装成完整分析。

### 六个 DFX 维度

| 维度 | Agent 从源码如何切入 | 主要测试输出 |
| --- | --- | --- |
| 功能与状态 | 外部入口、协议状态机、状态转换、配置生效 | 正常/异常功能、状态边界、非法转换 |
| 资源与规格 | 申请释放、计数、池、队列、配额、连接、缓存 | 上下限、超规格、回落恢复、泄漏、长稳 |
| 性能与压力 | 分配路径、队列深度、锁竞争、批处理、退化点 | 吞吐、时延、长尾、压力拐点、恢复时间 |
| 并发与异常 | 锁、原子、回调、超时、取消、销毁竞态 | 时序窗口、并发冲突、重复操作、异常清理 |
| 升级与兼容 | 版本、配置、持久状态、协议和固件矩阵 | 升级、回滚、跨版本、配置继承、兼容性 |
| 可靠性与一致性 | 故障传播、重连、重置、持久化和数据路径 | 故障注入、业务连续性、数据一致性、恢复代价 |

资源与规格会先进行轻量扫描。一旦命中申请、释放、计数、内存池、队列、连接、缓存或额度信号，或者用户明确强调，就进入资源规格与泄漏专项深挖。

## 如何向 Agent 描述任务

### 一个有效的任务契约

Agent 开始正式分析前会整理以下内容：

| 字段 | 说明 |
| --- | --- |
| 模式 | MR 回归或模块全量分析 |
| 目标 | 一个明确功能模块 |
| 仓库 | `pangea-data/repositories/` 下的登记名 |
| 版本/MR | 版本号、MR 链接和精确 commit |
| 组网 | 控制器、主机、链路、协议、卡件等环境 |
| 测试重点 | 资源、规格、性能、异常恢复等 |
| 输入材料 | 文档、存量用例、覆盖率、历史缺陷 |
| 排除范围 | 本次明确不分析的内容 |
| 深度 | focused、complete 或 fast |
| 已知缺口 | 缺失仓、缺失版本、工具或资料限制 |

信息充分时 Agent 会直接继续，不要求用户逐阶段确认。只有目标不清、输入冲突、仓库无法定位或版本无法绑定时才提问。

### 好的描述

```text
分析 driver 仓中的 NVMe-oF 连接恢复模块，版本 V3.2，双控组网。
重点看控制器复位期间新连接、超时回调和资源释放。
需要完整型模块分析，存量用例在 inbox/nvme-cases.xlsx。
```

### 过宽的描述

```text
把 BMC、BIOS、卡件、协议驱动和所有后端盘全部分析一遍。
```

建议拆为多个 Run。否则代码地图、版本关系和风险边界都难以闭环。

### 用户不知道版本或重点时

可以直接说明“不知道，请从 MR、commit 和源码反推”。Agent 会把反推结果写为推断，并提供验证路径，不会把推断冒充事实。

## 执行状态与内部分析

执行期间会看到中文状态行：

| 状态 | 含义 |
| --- | --- |
| `[梳理中 (._.)]` | 识别输入、仓库、版本和任务边界 |
| `[分析中 (｀・ω・´)]` | 建立代码地图、流程或影响链 |
| `[挖掘中 (ง •̀_•́)ง]` | 执行 DFX 扫描或专项深挖 |
| `[审核中 (¬_¬)]` | 去重风险、检查证据和用例可执行性 |
| `[发呆中 (－_－)]` | 等待 MCP、索引或子 Agent，并说明等待对象 |
| `[难过中 (；へ：)]` | 仓库、版本或证据存在无法闭环的缺口 |
| `[狂躁中 (╬ಠ益ಠ)]` | 工具连续失败，正在切换降级路径 |
| `[高兴中 (￣▽￣)b]` | 完成关键因果链或最终交付 |

状态只反映真实事件，不参与风险判断，也不会写入正式报告。

内部六个 DFX 子 Agent 共享同一份任务契约、代码地图和证据目录。子 Agent 只返回结构化风险卡；主 Agent 负责去重、跨维度合并、严重度、可信度、SFMEA、黑盒转译和最终用例。

## 报告与用例

每个完成的 Run 生成：

```text
pangea-data/reports/<run-id>/report.md
pangea-data/reports/<run-id>/report.html
```

两份报告内容一致。HTML 是完全离线的单文件，可直接在浏览器打开，支持：

- 关键词搜索。
- 按严重度筛选。
- 按 DFX 维度筛选。
- 按 Blackbox-ready、Graybox-ready、Developer-confirm 筛选。
- 风险与场景/用例双向跳转。
- 默认展开测试解释、折叠源码证据。

报告固定包含：

1. 任务契约。
2. 代码地图。
3. 入口与关键流程。
4. 异常分支。
5. 全量风险账本。
6. 测试场景。
7. 测试用例。
8. 风险覆盖映射。
9. 代码证据附录。
10. 未闭环项和下一步建议。

### 风险等级

- `Critical`：数据不一致或丢失、业务归零/断连、需要修卡、无法在线恢复等。
- `High`：核心功能受损、显著性能退化、持续资源泄漏或恢复代价很高。
- `Medium`：非核心功能受损，影响范围有限或存在明确规避方式。
- `Low`：低影响、低概率或防御性改进项。

严重度和可信度分开表达。一个后果很严重但证据不足的风险，可以是 `Critical + low confidence`，不会为了显得确定而抬高可信度。

### 转译状态

- `Blackbox-ready`：外部入口、触发、观测和恢复完整，可直接形成用例。
- `Graybox-ready`：需要日志、指标、诊断命令、故障注入或开发插桩。
- `Developer-confirm`：只有源码疑点，保留风险和证据，但不伪造测试用例。

## 断点恢复

每个阶段完成后，Agent 都会把事实、证据、风险和下一步写入 Run checkpoint。长任务不依赖聊天历史记住关键结论。

列出未完成 Run：

```text
/resume-run
```

恢复指定 Run：

```text
/resume-run mr-regression-iscsi-20260806-103000
```

恢复时 Agent 会读取：

- 任务契约。
- workflow plan。
- 已完成 checkpoint。
- 风险账本。
- 审计整改状态。
- 当前 Run 的只读代码快照。

MR Run 会继续使用原有快照，不会重新 checkout 源仓或悄悄换成新版本。必要快照失效时，Agent 会标记缺口并要求新的精确 commit/ref。

不要把新任务自动合并进旧 Run。目标、版本或模块变化明显时，应创建新的 Run。

## 黑盒、灰盒和插桩边界

### 允许的源码分析

Agent 可以在代码地图、流程、异常分支和证据附录中写函数名、变量名、调用链、状态机和代码位置。例如：

```text
源码证据显示资源回收路径遗漏了过载期间未计入可用额度的请求。
```

这些内容用于解释为什么存在风险。

### 用例必须面向外部行为

推荐：

```text
把并发连接数提升至内部规格上限以上，保持稳定压力后逐步回落到规格内；
持续观察新建连接成功率、业务 IOPS、资源诊断计数和无需重启的恢复时间。
```

禁止：

```text
调用 update_ready() 并断言返回值为 0。
```

禁止生成：

- 单元测试。
- Mock、Stub、Fake、Spy 测试。
- 直接给内部变量赋值的测试步骤。
- 以函数返回值或内部实现状态为最终判据的白盒用例。
- 修改被测源码的插桩代码。

### 插桩需求

允许把开发提供的插桩作为系统测试控制手段。Agent 可以提出需要开发补充的控制点，但不生成实现代码。

示例：

```text
前置条件：开发提供“接收准备状态延迟生效”插桩。
步骤：将 iSCSI 接收准备状态延迟 2 秒生效；主机完成连接建立后，在该时间窗发送 Data 报文。
观测：连接是否异常断开、业务是否卡住、超时后是否自动恢复，以及协议和资源诊断信息。
恢复：关闭插桩并重新建立连接，确认无需重拉用户态进程即可恢复。
```

正式步骤优先描述“控制什么外部时间窗”和“观察什么系统行为”。变量名或内部位置只放在插桩需求和证据说明中。

## 文件和 Run 管理

`pangea-data/` 是唯一个人数据根。目录按用途分为四类：

```text
pangea-data/
  inbox/                         # 用户放入的原始资料
  repositories/                  # 用户复制或 clone 的只读 Git 仓库
  library/                       # 有资料导入后才创建
    sources/                     # 内容哈希归档原件
    markdown/                    # 转换后的 Markdown
    assets/                      # 文档图片等转换资产
    catalog.jsonl                # 资料目录、锚点与分类
  indexes/                       # 有索引任务后才创建；records + shadows
  runs/<run-id>/                 # 历史 Run 记录和中间工件，不是用户交付目录
    manifest.json
    internal/                    # 任务契约、风险账本、workflow plan、报告模型、审计意见
    checkpoints/                 # 首次 checkpoint 后才创建
    evidence/                    # 只有实际证据文件时才创建
    tmp/                         # 续跑快照等临时内容；完成后清理并删除空目录
  reports/<run-id>/              # 用户唯一需要查看的正式交付目录
    report.md
    report.html
```

`/initial` 的 `workspace_inventory` 会分别列出正式报告、Run 历史和旧版报告。一个 Run 只有在 `finalize-v2` 返回的两个报告文件真实存在、非空并写入 manifest `deliverables` 后才算完成。对话中的总结、`internal/report-model.json`、checkpoint 和审计 JSON 都不是正式报告。

根目录旧 `source/`、`inputs/`、`workspace/`、`outputs/`、`projects/`、`runs/` 六区模式已经退役并从仓库删除。为保护本地遗留数据，它们仍被 Git 忽略；`/initial` 只报告迁移缺口，不自动移动或删除文件。

## 安全边界

PANGEA-TEST 对代码仓执行只读分析：

- 主 Agent 和 DFX 子 Agent 的编辑权限为拒绝。
- 不在代码仓执行提交、暂存、reset、stash、强制 checkout 或格式化。
- MR 分析使用当前 Run 内的 commit 只读快照，不直接在源仓切版本。
- GitNexus 只索引 `pangea-data/indexes/shadows/` 中的受管 shadow clone。
- 文档归档、转换、checkpoint 和报告写入均限制在 `pangea-data/` 受管目录。
- 路径、符号链接、快照 commit、Run provenance 和报告对一致性均有运行时门禁。

`pangea-data/` 已被 Git 忽略，不会把内网代码、文档或分析报告提交到本项目仓库。

## 常见问题

### 1. 切到 `pangea-test` 后感觉和普通 OpenCode 没区别

先确认：

```bash
python3 runtime/doctor.py
```

然后检查客户端 Agent 列表中当前选中项确实是 `pangea-test`。真正进入正式流程后，应看到任务契约、中文阶段状态、Run ID、内部 DFX 调度和最终审计，而不是只有泛化聊天回复。

如果客户端没有加载 `.opencode/`，切换名字本身不会产生 PANGEA-TEST 体验。

### 2. 仓库无法识别

检查仓库是否位于：

```text
pangea-data/repositories/<登记名>/
```

并确认它是真实 Git 工作树，不是符号链接，也不是某个上层 Git 仓中的普通文件夹。

### 3. new session 没有自动拉取代码

常见原因：

- 工作区有未提交修改。
- detached HEAD。
- 本地和远端已分叉。
- 认证失败。
- 当前分支不能 `--ff-only` 更新。

Agent 会跳过 pull，避免破坏用户仓库。请用户自行处理 Git 状态后重新开 session 或执行 `/initial`。

### 4. MR 无法进入审计或生成报告

MR Run 要求每个契约仓只有一个权威快照，并且仓名和 40 位小写 commit SHA 与任务契约完全一致。常见问题包括：

- MR MCP 没有返回精确 commit。
- 本地仓不存在该 commit。
- 创建了旧 commit 快照。
- 关联 submodule 缺口没有进入任务契约和报告。
- 必需阶段未完成。

使用 `/resume-run <Run ID>` 查看下一阶段和缺口，不要手工伪造 PASS 审计意见。

### 5. 文档一直显示待转换

- PDF：安装并重新探测 `pdftotext`，扫描件可能仍需 OCR 后重新导入。
- `.doc/.xls/.ppt`：先由用户转换为现代 Office 格式。
- 加密或损坏文件：在外部修复或导出为可读格式。

PANGEA-TEST 不会把解析失败的空文本冒充成功转换。

### 6. GitNexus 索引很慢

首次 `/initial` 可能为每个仓建立受管 shadow clone 和索引。后续是否增量由实际 GitNexus 能力决定。可以先使用 `/module-analysis ... --fast`，但快速型只降低分析深度，不取消六维风险扫描。

### 7. 报告没有生成

只有以下条件全部满足才会写入 `final/`：

- 所有必需阶段完成。
- 风险账本和报告模型一致。
- 任务契约与当前 Run 一致。
- 独立 auditor 返回 `PASS`。
- `required_actions` 为空。
- PASS 后报告模型没有再次变化。

执行 `/resume-run <Run ID>` 查看是分析、整改、审计还是报告阶段未完成。

### 8. 长任务发生上下文压缩，会不会丢风险

每个阶段、每批子 Agent 汇总和每轮审计整改前都会先写 checkpoint 与风险账本。恢复时以这些工件为准，不依赖聊天记忆。原生自动压缩只是最后降级路径。

### 9. Agent 会不会生成或修改测试代码

不会生成白盒单测、Mock/Stub 或被测源码补丁。当前交付是测试分析、场景、黑盒用例、灰盒观测和插桩需求。

## 维护与诊断命令

普通用户优先使用 Agent 命令，不需要手工编排 Run。以下命令用于诊断或二次开发。

### 健康检查

```bash
python3 runtime/doctor.py
```

### 查看 CLI

```bash
python3 runtime/runctl.py --help
python3 -m tooling.pangea_cli --help
```

### 查看未完成 Run

```bash
python3 -m tooling.pangea_cli data incomplete-runs
```

### 查看指定 Run 的续跑计划

```bash
python3 runtime/runctl.py resume-v2 --run-id <Run ID>
```

### 手工创建模块分析 Run

```bash
python3 runtime/runctl.py create-v2 \
  --scenario module-analysis \
  --target "iSCSI 连接管理" \
  --repository driver \
  --analysis-depth complete \
  --version V8R2C10 \
  --topology "双控阵列，多主机多路径" \
  --test-focus "资源规格与异常恢复"
```

### 手工创建 MR Run

```bash
python3 runtime/runctl.py create-v2 \
  --scenario mr-regression \
  --target "iSCSI 连接管理" \
  --repository driver \
  --repository-commit driver=<40位小写SHA> \
  --mr-url "https://git.example.com/storage/driver/-/merge_requests/123" \
  --analysis-depth focused
```

同一参数如 `--repository`、`--test-focus`、`--input-ref`、`--exclude`、`--known-gap` 和 `--signal` 可重复传入。MR 模式中每个仓必须有且只能有一个同名 `--repository-commit`；模块模式禁止该参数。

### 运行测试

```bash
python3 -m unittest discover -s tests
PANGEA_VALIDATOR=stdlib python3 -m unittest discover -s tests
PANGEA_VALIDATOR=jsonschema python3 -m unittest discover -s tests
python3 -m pytest -q
```

## 当前边界和后续方向

当前版本已完成 MR 回归和模块全量测试分析的工作流、文件运行时、黑盒报告和独立审计。以下内容不在当前正式入口中：

- 登录真实主机阵列组网自动执行用例。
- 自动生成开发插桩代码。
- 白盒单元测试生成。
- 独立补丁测试策略入口。
- CodeTalk 联动。
- Claude Code 原生适配。

项目设计与实现细节见：

- [需求说明书](docs/requirements.md)
- [Architecture v2](docs/architecture.md)
- [v2 迁移说明](docs/iterations/006-architecture-v2-migration.md)
- [内网待办清单](docs/内网待办清单.md)
