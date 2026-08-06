# PANGEA-TEST

PANGEA-TEST 是面向平台驱动测试团队的个人测试 Agent。它不是专家导航页：用户只面对一个 `pangea-test`，Agent 阅读 MR、diff、提交记录和本地只读代码仓，追踪影响链，把源码风险转成可执行的黑盒测试场景、灰盒插桩需求和回归建议。

首版聚焦两个任务：

- `/mr-regression`：MR 原场景回归、改动功能验证、影响链回归、异常与恢复验证。
- `/module-analysis`：选定功能模块的全量测试分析；默认完整型，`--fast` 降低调用链和分支展开深度。

Architecture v2 的工作流入口只有以上两项。旧的 `module-full-analysis` 已退役；顶层 CLI 不再暴露 `project`、`input`、`asset` 或 `workflow` 域，因此不会创建 `workspace/` 或 `outputs/` 旧协议任务。两条工作流由正式 Agent 命令或 `runtime/runctl.py create-v2` 创建。

补丁测试策略是下一优先级，暂不作为独立入口。日志定位、抓包、用例评审和缺陷单能力只作为这两个工作流中的分析手段保留。

## 使用方式

在本项目目录启动 OpenCode 或 CodeAgent，切到唯一用户入口 `pangea-test`。可直接描述任务，也可使用显式命令：

```text
/initial
/setup-tools
/mr-regression <MR 链接或说明>
/module-analysis <功能模块> [--fast]
/resume-run
```

开始正式分析时，Agent 先展示任务契约：模式、目标、仓库和版本、组网、测试重点、可用资料、排除范围与工具能力。信息充分即继续；关键歧义才会要求确认。MR 背景缺失时，Agent 必须从 MR、diff、commit 和源码反推，并区分事实、推断和待确认项。

## 核心体验

- 主 Agent 以测试架构师为主身份，并具有灰盒源码分析能力。
- 运行期间显示真实阶段状态：`梳理中 (._.)`、`分析中 (｀・ω・´)`、`挖掘中 (ง •̀_•́)ง`、`审核中 (¬_¬)`、`发呆中 (－_－)`、`难过中 (；へ：)`、`狂躁中 (╬ಠ益ಠ)`、`高兴中 (￣▽￣)b`。状态由阶段切换、关键发现、等待、降级和完成等真实事件触发；同一阶段连续更新会去重，不写入正式报告。
- 内部并发调用六个 DFX 子 Agent：功能与状态、资源与规格、性能与压力、并发与异常、升级与兼容、可靠性与一致性。
- 所有子 Agent 使用共同的 C/C++ 源码分析底座和统一风险卡，主 Agent 去重、定级、完成内部 SFMEA 并生成用例。

## 黑盒优先

源码分析可以使用函数、变量、调用链和状态机作为证据；正式报告先给测试解释。用例主体必须用外部可执行语言描述协议命令、CLI、REST API、主机/阵列/卡件操作、日志、指标和诊断命令。

允许系统级故障注入、时序控制和测试插桩。插桩需求可写明内部控制点、控制语义、参数、观测和恢复要求，但 Agent 不生成插桩代码，也不改源码。禁止生成单元测试、Mock、替换依赖的 Stub 或以源码函数断言为主体的白盒用例。

## 工作空间与安全

运行数据位于项目内、被 Git 忽略的 `pangea-data/`：

```text
pangea-data/
  inbox/                     # 用户导入的 Word、Excel、PPT 等原始资料
  library/{sources,markdown,assets,catalog.jsonl}
  repositories/              # 已登记的只读分析代码仓，以目录名作为仓名
  indexes/                   # GitNexus 索引记录及受管 shadow clone
  runs/<run-id>/{manifest.json,checkpoints,evidence,internal,tmp,final}
  registry/
```

new session 会发现新资料、增量转换为带页/Sheet/幻灯片锚点的 Markdown、归类并索引；首次实际使用未入库资料时也会触发导入。正式 Run 的 `--repository` 参数只接受 `pangea-data/repositories/` 下已登记代码仓的目录名。MR 创建还必须为每个仓提供 `--repository-commit <仓名>=<40位小写SHA>`；任务契约和只读快照须精确匹配该仓名与 commit，旧版本不能通过完成门禁。模块分析不得提供该参数。代码仓只在工作区干净且分支关系正常时自动 `git pull --ff-only`；绝不提交、stash、reset、强制切换或解决冲突。GitNexus 仅分析 `indexes/shadows/` 下由 PANGEA 创建的 `--no-hardlinks` shadow clone，绝不将源仓作为可变索引目标；工具缺失时记录降级。MR 版本可复制到 Run 临时目录分析，完成或下次启动时清理。

完成事实需要可复核内容：通用分析阶段的每个 fact 都有具体 `summary` 与 `evidence`，`dfx_scan` 和 `mr_baseline` 使用各自的结构化事实，报告事实同时记录 `report_md` 和 `report_html`。审计整改的每项 evidence 是 `{artifact, location, verification}` 对象，其中 artifact 必须是 Run 内相对安全路径；占位、纯符号和机械重复文本不会通过门禁。

## 交付物

每次 Run 的权威交付物是内容一致的：

- `report.md`
- `report.html`：完全离线的单文件报告，支持搜索、按严重度/DFX/转译状态筛选，以及风险和用例双向跳转。

报告包含任务契约、代码地图、关键流程、异常分支、全量风险账本、测试场景、用例、覆盖映射、代码证据附录和未闭环项。HTML 默认展开测试解释，折叠源码证据；流程图使用 Mermaid 预渲染并保留文字版。

## 验收

自动验收使用公开仓和隐藏答案集，例如 SPDK、UCX、RDMA Core、OpenBMC。评估缺陷模式命中、全量风险覆盖广度、源码证据、影响链、可执行触发/观测/恢复、严重度合理性及黑盒语义纯度。还应验证源码零写入、脏仓跳过更新、Run 恢复、临时目录清理和离线 HTML。

详见 [需求说明书](docs/requirements.md)、[Architecture v2](docs/architecture.md) 和 [v2 迁移说明](docs/iterations/006-architecture-v2-migration.md)。旧迭代文档仅保留为历史记录。
