# PANGEA-TEST 需求说明书 v2

> 状态：冻结。本文是 Architecture v2 的产品契约；与旧 v1 文档冲突时以本文为准。

## 1. 产品边界

**R1 唯一入口。** 用户只面对 `pangea-test`。它是测试架构师，具备灰盒源码分析能力；差异来自分析协议、证据和交付，不来自角色扮演或 Tab 分流。

**R2 首版场景。** 仅正式开放 `/mr-regression` 与 `/module-analysis`。补丁测试策略是下一优先级。日志、抓包、缺陷单、用例评审不再作为独立入口，可被两个核心工作流调用。

**R2.1 CLI 入口收敛。** 顶层 CLI 只开放 `data`、`report`、`tool`、`library`、`repo`、`index` 六个 v2 域；`project`、`input`、`asset`、`workflow` 必须不可达，直接执行历史 `workflowctl` 模块也必须拒绝。`module-full-analysis` 是已退役名称，registry 不保留别名。两条正式工作流只由 Agent 命令或 `runctl create-v2` 创建 `pangea-data/runs/` 下的 v2 Run；CLI 不得创建旧 `workspace/`、`outputs/` 或调用 `runctl init`。

**R3 范围。** 服务集中式阵列、块/文件存储、BMC、BIOS、卡件、后端盘及协议/卡件驱动、微码等组件；优先 C/C++，后续扩展 Luna、Go 等。一个任务原则上选一个功能模块。跨仓缺失时先完成可见仓分析，并标记覆盖缺口和下一步建议。

## 2. 工作流

**R4 任务契约。** 每次正式任务先产出并保存任务契约：任务模式、对象、仓库/版本、组网、重点、输入、排除范围、工具能力和深度。MR 必须为每个已登记仓绑定 `repository_commits` 中同名的 40 位 SHA，创建 CLI 必须逐仓提供；模块模式不得携带该字段。信息充分自动开始；目标不清、访问失败或关键输入冲突时才暂停确认。

**R5 MR 回归。** 必须包含原场景回归、改动功能验证、影响链回归、异常与恢复验证。MR、diff、commit、仓库是主要证据；缺少问题背景时从这些材料反推，明确标记事实、推断、待确认。升级、规格、性能、并发、资源等专项按变更证据路由，而非固定全扫。

**R6 模块全量分析。** 依次完成独立 inventory、obligation ledger、代码地图、关键流程、异常分支、广谱风险扫描、专项深挖、内部 SFMEA、场景与用例。默认完整型；`--fast` 保留全部 obligation 与六维 capability pack 覆盖，但减少调用链与分支展开并声明深度边界。每维独立记录具体结论、证据化 N-A 或待验证；沉默和空数组不能表示已分析。资源规格/泄漏在模块分析中必扫，命中信号或用户强调时深挖。

**R6a 阶段事实与整改门禁。** `code_map`、`flow`、`branches`、`impact_chain`、`dfx_route`、`risk_ledger`、`specialist`、`sfmea`、`test_design` 的每个 completed fact 必须有具体、非占位、非机械重复的文本 `summary` 与 `evidence`；布尔或数字不是事实。`report` fact 必须同时引用 `report_md` 和 `report_html`。每个 rework closure 使用 `evidence: {artifact, location, verification}`，artifact 只能是无绝对路径或 `..` 的 Run 相对路径，三字段均须具体且 closure 不得与 verification 机械重复；rework fact 使用具体 `rework_summary`。

**R7 连续执行。** 阶段仅是内部检查点，不逐阶段停问。每个 completed checkpoint 的每个 fact 都必须是包含非空有效值的对象。完整风险都进入账本，严重度为 `Low`、`Medium`、`High`、`Critical`，并与可信度分开；审计和完成前报告模型 canonical 风险必须与账本的 `risk_id` 集合和关键字段逐项一致。

## 3. 内部协作

**R8 角色与能力包收敛。** 活动角色固定为 `pangea-test` primary、通用 `analysis-worker`、独立 `auditor`，MR 条件使用 `mr-reader`。功能与状态、资源与规格、性能与压力、并发与异常、升级与兼容、可靠性与一致性固定为 capability packs，不单建 Agent；可观测性与可维护性贯穿全部风险。

**R9 独立覆盖分母与 worker 协议。** 确定性运行时必须先从冻结快照生成 source inventory 与 obligation ledger，再按 obligation/range 生成 immutable context packs。每个 obligation 必须恰好由一个 fragment 处置；worker 只读取被分配范围，输出严格 `analysis_fragment` JSON，不得自派 Task、扩大范围或用摘要替代模型贡献。无效 JSON、截断、receipt 不闭合和 disposition 缺失必须失败。共同底座覆盖 C/C++ 调用链、状态机、所有权、清理、初始化/卸载、并发和测试语义转译；Storage Skills 与厂商知识按源码证据门控加载并留下 receipt。

**R10 风险卡与完整模型。** worker fragment 同时提交 Flow/Branch/State/Resource/Concurrency/Error Chain/Scenario contribution、事实、风险与 N-A/待验证。风险卡包含代码信号、因果链、触发、传播、外部后果、观测、恢复、严重度、可信度、转译状态、插桩需求和源码证据。primary 负责经验证合并、去重、SFMEA、覆盖和用例，但不得把完整分析模型压缩成风险卡或丢失 High/Critical contribution。

## 4. 测试语言与交付

**R11 黑盒优先。** 用例使用协议命令、CLI、REST API、主机/阵列/卡件操作、日志、指标、诊断命令和故障注入等测试语义。允许少量灰盒内容。代码地图、流程、分支可出现函数和变量，但必须同时解释其外部行为和测试意义。

**R12 插桩边界。** 允许系统级插桩、故障注入与时序控制。Agent 可提出开发需提供的控制点、控制语义、参数、观测、恢复要求及 `待插桩准备` 状态；不生成插桩代码。禁止单元测试、Mock、替换依赖的 Stub 和白盒函数级测试用例。

**R13 转译状态。** `Blackbox-ready` 有完整外部入口、触发和判据；`Graybox-ready` 依赖日志、指标、诊断或插桩；`Developer-confirm` 只有代码疑点。全部风险写入报告，前两者才生成可执行场景。

**R14 报告。** 每个 Run 生成内容一致的 `report.md` 和离线单文件 `report.html`。报告必须有任务契约、代码地图、关键流程、异常分支、风险账本、场景、用例、风险-用例映射、证据附录、未闭环项。HTML 支持搜索、筛选和双向跳转，测试解释默认展开，源码证据默认折叠。

## 5. 数据、上下文与安全

**R15 数据布局。** `pangea-data/` 是唯一运行数据根，包含 `inbox/`、`library/`、`repositories/`、`indexes/`、`runs/`、`registry/`。资料按 hash 去重，原件只读保留；转换保留页码、Sheet、单元格、幻灯片和图片锚点。模型不支持视觉时，图片标记为未解析视觉证据。

**R16 会话行为。** new session 扫描新资料与未完成 Run，供用户选择恢复或新建；新资料和新代码可触发增量导入。仓库仅在干净且可快进时 `git pull`，否则跳过并说明原因。分析源码全程只读、不得提交；MR 临时副本在 Run 后清理。

**R17 上下文账本。** 不依赖模型原生压缩。阶段检查点保存任务契约、事实、风险、覆盖、用例索引、决策和下一步；永久保护具体数字、源码位置、因果链、高风险与未闭环项，丢弃重复叙述、探索噪音和已推翻猜测。

**R18 工具。** GitNexus 与静态分析是增强能力。`/initial` 探测版本、增量能力和环境；`/setup-tools` 才允许用户显式安装/启用内网可用工具。缺失时退化到源码搜索和人工调用链分析，并报告影响；不自动联网安装、不使用容器。

## 6. 运行状态、知识与验收

**R19 中文状态。** 执行时持续显示机器状态的中文情绪外观，例如梳理、挖掘、审核、等待、降级、完成。高兴、难过、狂躁等必须由真实事件触发并限频；不进入正式报告。

**R20 知识沉淀。** 资料可自动整理，但 Agent 推断不得自动升格为团队经验。跨 Run 经验须有明确来源、复现结果或多源验证，保留来源、适用版本和可信度；当前版本源码与历史材料冲突时标记差异并必要时提出开发确认。

**R21 自动验收。** 使用公开代码仓的 PR/MR 与隐藏缺陷结论测试 Agent。评分包括已知模式命中、全量风险覆盖、源码证据、影响链、触发/观测/恢复、严重度、黑盒纯度；并测试只读源码、脏仓更新跳过、恢复、临时清理和离线报告。

**R22 本地信任边界。** 机器门禁用于阻止流程遗漏和工件漂移，不为拥有本机写权限的调用者提供密码学身份认证，也不伪造 token。审计独立性由 `pangea-test` 编排契约保证：必须调用隐藏只读 `auditor`，由其在固定报告模型绑定上输出独立意见。

## 7. 退役项

历史 `dev-expert`、`troubleshooter`、`test-designer` 退出可切换入口，不保留为活动角色。其有用能力下沉为 skills、方法和内部阶段。保留 `core/`、`runtime/` 中可验证、只读、安全、证据和恢复相关资产，按 v2 契约演进；旧 registry、工作流和报告格式不得作为 v2 行为依据。


**R23 完整型分析深度。** `module-analysis --analysis-depth complete` 必须在报告审计前通过 `stage-analysis-v2`，生成固定 `internal/analysis-model.json`。模型必须逐项覆盖入口、完整 Flow Card、分支、状态、资源、并发、错误传播、场景候选、SFMEA、测试流程、用例、追溯和 Coverage disposition。风险卡、阶段摘要或六句 DFX 结论不能替代分析模型。`fast` 必须记录明确 `depth_limitations`。
