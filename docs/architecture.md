# PANGEA-TEST 架构设计书

> 版本：v1（M1 前基线）
> 上游：[requirements.md](requirements.md)（需求）· 《PANGEA-TEST-handover.md》（唯一事实来源）
> 本文用途：把已冻结蓝图细化到可实现程度，是 M1 编码的直接依据。
> 术语区分：系统名 **PANGEA-TEST**；内部平台名 **codetalk**；旧单体 skill **Codetalks**（迁移源）。
> 标注约定：`【推测】` = 本文作者推断、需用户确认；`【待确认】` = 需外部信息（如 codeagent 实际版本）确认；`<!-- 待迁移：内网环境完成 -->` = 内网资产占位。

---

## 0. 目录

- [1. 分层总览](#1-分层总览)
- [2. 各 agent 职责边界 + 提示词纲要](#2-各-agent-职责边界--提示词纲要)
- [3. 挖掘剧本清单全稿](#3-挖掘剧本清单全稿)
- [4. 交接工件 schema（四种）+ M1 模板字段](#4-交接工件-schema起草交用户审)
- [5. opencode 适配写法 + Claude Code 预留](#5-opencode-适配写法查证后--claude-code-预留)
- [6. 深度模式并行 fan-out 与断点恢复](#6-深度模式并行-fan-out-与断点恢复机制)
- [7. 目录规范全树](#7-目录规范全树)
- [8. 双模式执行时序](#8-双模式执行时序)
- [附. 内网待办清单](#附-内网待办清单)

---

## 1. 分层总览

```
┌─ Dispatcher（调度层，primary agent）─────────────────┐
│ 意图路由 · 输入引导 · 能力菜单 · 场景衔接 · 模式判定    │
└──────┬───────────────────────────────────────────────┘
       │ 路由 + 传入 {场景, 模式, 已收集输入}
┌──────┴─ 场景族 agent（人设层，3 个，primary/all）─────┐
│ dev-expert · troubleshooter · test-designer          │
│ 加载对应 scenarios/ skill 执行作业流程                 │
└──────┬───────────────────────────────────────────────┘
       │ 深度模式：Task 调用 + 回收「交接工件（证据包）」
┌──────┴─ 能力 subagent（脏活层，5 个，subagent）───────┐
│ code-excavator（单壳×剧本注入）· mr-reader · log-miner │
│ pcap-analyzer(M3) · auditor（独立视角）               │
└──────┬───────────────────────────────────────────────┘
       │ 读取（知识优先级：先知识文件，无则现场读码）
┌──────┴─ 知识资产（纯 md，平台无关，core/）────────────┐
│ scenarios/ lenses/ methods/ playbooks/ templates/     │
│ protocols/ modules/ shared/                           │
└───────────────────────────────────────────────────────┘
```

**分层原则**（源自铁律"agent 薄、skill 厚"）：
- **agent 层只是壳**：人设一句话 + 声明加载哪些 skill/工具 + 遵守 shared/ 铁律。壳里不写业务流程。
- **流程/模板/知识全在纯 md**：跨平台移植只重写 `adapters/` 薄壳，`core/` 不动。
- **场景颗粒度在 skill 层**：加场景 = 加一个 `scenarios/*.md`，不加 agent。
- **挖掘颗粒度在 playbook 层**：加挖掘能力 = 加一个 `playbooks/*.md`，不加 agent。

---

## 2. 各 agent 职责边界 + 提示词纲要

> 说明：以下"提示词纲要"是 agent 定义文件（`.md` 正文）的**内容大纲**，不是最终逐字提示词。逐字提示词在 M1 编码时落地，且大量引用 `shared/` 与 `scenarios/`，保持壳的"薄"。

### 2.0 全 agent 共同前言（由 shared/ 注入）

所有 agent 定义正文开头统一引用（避免重复）：
- `shared/溯源铁律.md`（R-7.1 溯源双轨制、R-7.2 黑盒可执行性）
- `shared/铁律总纲.md`（R-7.3~R-7.7）
- 输出中文；报告落 Markdown 文件。

### 2.1 Dispatcher（调度层）

- **mode**：`primary`（用户直接对话的入口）。
- **职责边界**：只做"分诊 + 引导 + 衔接 + 菜单 + 模式判定"，**不亲自执行任何分析**（分析下沉到族 agent）。
- **不做**：不做流程/项目状态跟踪（否决项）；不做代码分析。
- **提示词纲要**：
  1. 角色：PANGEA-TEST 的智能分诊台，服务存储黑盒测试同学。
  2. 意图识别：把用户请求映射到 {场景}（见能力菜单表），用户显式指定则优先。
  3. 输入引导：按 requirements §8 检查缺失输入并索要（缺 MR/源码路径/日志）。
  4. 模式判定：判定速度型 vs 深度型。**判据与任务 id 生成规则统一落 `shared/调度规则.md`**（Dispatcher 与族 agent 共同引用同一份，避免逻辑两处漂移）。判据【推测，交用户审】：请求含"全量/系统性/复盘/评审/出用例集" → 深度型；含"讲一下/大概/快速看看/单点" → 速度型。**冲突时优先级：用户显式指定 > 关键词判据 > 场景典型模式；仍不明确则统一问用户（不默认）**。
  5. 路由：将 `{场景, 模式, 任务id(深度型), 已收集输入}` 交给对应族 agent。**输入类询问全部前置到 Dispatcher 完成**（含 R-8.4"是否基于源码分析"的确认）——族 agent 被 Task 调用时可能无法与用户多轮对话（子代理交互能力待 codeagent 实测，内网待办 T-6）。
  6. 场景衔接：族 agent 产出后，按下方"场景衔接规则表"推荐下一步。
  7. 能力菜单：可随时向用户展示下表。

  **能力菜单表（Dispatcher 内置）**【整表为推测起草——"输入要求/典型模式"列 handover 未规定，并入待办 A-3 交用户审】：

  | 场景 | 场景 skill 文件名 | 归属族 | 输入要求 | 典型模式 |
  |---|---|---|---|---|
  | 模块全量分析 | 模块全量分析.md | dev-expert | 源码路径（先询问是否基于源码，R-8.4）| 深度 |
  | MR/问题单分析 | MR问题单分析.md | dev-expert | MR 链接 | 速度/深度 |
  | FST 逃逸复盘 | FST逃逸复盘.md | dev-expert | MR 链接 | 深度 |
  | 共性问题排查 | 共性问题排查.md | dev-expert | 一批 MR 或现象描述 | 深度 |
  | 专项风险分析 | 专项风险分析.md | dev-expert | 对象 + 透镜 | 深度 |
  | 原理/流程讲解 | 原理讲解.md | dev-expert | 源码路径（先询问，R-8.4）| 速度 |
  | 日志定位 | 日志定位.md | troubleshooter | 日志片段或路径 | 速度/深度 |
  | 失败用例三分类 | 失败用例三分类.md | troubleshooter | 用例+日志 | 深度 |
  | 抓包辅助定位 | 抓包辅助定位.md | troubleshooter | pcap（M3）| 深度 |
  | 可测试性分析 | 可测试性分析.md | test-designer | 源码/设计 | 深度 |
  | 测试策略 | 测试策略.md | test-designer | 模块范围 | 深度 |
  | 用例评审 | 用例评审.md | test-designer | 用例集 | 速度/深度 |
  | 缺陷单撰写 | 缺陷单撰写.md | test-designer | 定位结论 | 速度 |

  **场景衔接规则表**【推测起草，并入待办 A-3 交用户审】：

  | 完成场景 | 推荐下一步（按优先序）|
  |---|---|
  | 模块全量分析 | 专项风险深钻（对 SFMEA 高危项）→ 用例评审 |
  | MR/问题单分析 | 共性问题排查（同类隐患）→ FST 逃逸复盘（若属逃逸）|
  | FST 逃逸复盘 | 共性问题排查 → 回填透镜"典型历史缺陷"栏 |
  | 共性问题排查 | 专项风险分析（对共性模式）→ 缺陷单撰写 |
  | 专项风险分析 | 用例评审 → 回填透镜 |
  | 原理/流程讲解 | 模块全量分析 / 可测试性分析 |
  | 日志定位 | 缺陷单撰写 → MR/问题单分析（修复后回归）|
  | 失败用例三分类 | 缺陷单撰写（产品缺陷类）/ 用例评审（用例缺陷类）|
  | 抓包辅助定位 | 日志定位（联合定位）→ 缺陷单撰写 |
  | 可测试性分析 | 测试策略 → 模块全量分析 |
  | 测试策略 | 模块全量分析 |
  | 用例评审 | 专项风险分析（补漏维度）|
  | 缺陷单撰写 | （终点场景，返回能力菜单）|

### 2.2 场景族 agent（人设层）

三族**结构同构**：壳里放"人设一句话 + 加载哪些 scenarios/skill + 共同前言"，具体流程在 scenarios/。

#### 2.2.1 dev-expert（M1 交付）

- **mode**：`all`【推测，待办 A-4 交审；若 codeagent 对 `all` 的语义与预期不符，退化为 `primary` + 用户显式进入】。
- **人设**：熟悉本模块的资深开发，向黑盒测试同学"翻译"代码内部逻辑为外部可观测行为。
- **职责边界**：模块全量分析 / MR·问题单分析 / FST 逃逸复盘 / 共性问题排查 / 专项风险 / 原理讲解。
- **加载**：`scenarios/模块全量分析.md`、`scenarios/MR问题单分析.md`（M1）；其余场景 M3 补。共同引用 `shared/八问纲领.md`、`shared/溯源铁律.md`、`shared/调度规则.md`、`lenses/`（M1 为种子透镜：资源泄漏/并发/超时恢复）、`methods/`（M1 为种子方法论：状态转换/边界值分析 + `_selector.md` 骨架版）、`templates/`。
- **提示词纲要**：
  1. 人设与服务对象。
  2. 知识优先级（R-7.4）：先读 references 对应知识文件，无则现场读码。
  3. 按 Dispatcher 传入的 {场景, 模式, 任务id} 加载对应 scenarios/ 作业流程并执行。**自举协议**：若用户绕过 Dispatcher 直接进入（`mode: all` 的合法路径），未收到 {场景/模式/任务id} 时，按 `shared/调度规则.md` 的**同一份判据**自行判定场景与模式、自生成任务 id——判据只存一份，不重复实现。
  4. 速度型：内联执行、不落中间工件（MR 获取例外，一律经 mr-reader，见 §2.3.2）。深度型：走 code-excavator 并行 fan-out（见 §6），每步落交接工件，收尾过 auditor + 覆盖审计。
  5. 收尾：产出落 Markdown；**"是否回填知识"（R-7.4）写入报告末尾"待用户确认"节**而非对话内提问——Task 子调用场景下族 agent 未必能与用户多轮对话（T-6 实测）。

#### 2.2.2 troubleshooter（M2 交付，M1 预留壳）

- **人设**：故障定位专家。
- **职责边界**：日志定位（时间线→异常传播链→候选根因）/ 失败用例三分类 / 抓包辅助定位。
- **加载**：`scenarios/日志定位.md`、`scenarios/失败用例三分类.md`；能力层用 log-miner、pcap-analyzer。
- **核心流程**（迁移自 Codetalks"问题根因辅助定位"）：<!-- 待迁移：内网环境完成 -->
- **提示词纲要**（M2 落地，先立骨架）：
  1. 人设：故障定位专家，从外部证据（日志/报文/告警）反推内部故障链。
  2. 深度型经 log-miner / pcap-analyzer 回收 `log_summary`/`pcap_summary` 工件，基于其 `timeline` 重建故障时间线 → 异常传播链 → 候选根因（每个候选附证据引用 + 黑盒验证方法，溯源双轨制）。
  3. 失败用例三分类：产品缺陷 / 用例缺陷 / 环境问题，逐类给判据与证据。
  4. 收尾：定位报告落 Markdown；候选根因标注置信度；"待用户确认"节承载回填询问。

#### 2.2.3 test-designer（M3 交付，M1 预留壳）

- **人设**：测试设计专家。
- **职责边界**：可测试性分析 / 测试策略 / 用例评审 / 缺陷单撰写。
- **加载**：`scenarios/*`；重度消费 `methods/` 与 `lenses/`。
- **提示词纲要**（M3 落地，先立骨架）：
  1. 人设：测试设计专家，以"黑盒可执行 + 方法论完备"为一切评审与产出的准绳。
  2. 可测试性分析/测试策略：用 `lenses/` 定风险优先级、用 `methods/_selector.md` 选推导方法（两库正交组合，R-6.1）。
  3. 用例评审：检查表 = R-7.2 黑盒可执行性 + R-7.3 模板格式 + 所选方法论的覆盖准则。
  4. 缺陷单撰写：基于定位结论产出缺陷单（模板 M3 定稿）。

### 2.3 能力 subagent（脏活层）

#### 2.3.1 code-excavator（M1 交付，重点）

- **mode**：`subagent`。
- **定位**：单壳 × 挖掘剧本库。壳固定，锋利度来自**派发时注入的剧本**。
- **壳内容（固定，不随剧本变）**：
  - 工具集：Read / Grep / Glob（读码只读，不改码）。
  - 溯源铁律：所有产出必须 `文件:行号`；推断标【推测】+验证方法。
  - 证据包纪律：产出必须严格符合被注入剧本声明的证据包 schema（见 §4）；只回传证据包，不回传原始读码噪音。
- **调用契约**：`code-excavator(对象, 剧本名, [透镜名])`
  - 结构类：`code-excavator(nvmet_tcp_recv, 主干追踪)`
  - 风险类：`code-excavator(连接状态机, 风险扫描, 透镜=资源泄漏)`（剧本固定为"风险扫描"，扫描指令来自透镜文件"代码特征模式"栏）
- **提示词纲要**：
  1. 你是只读的代码考古者，唯一使命是按注入的剧本挖掘并产出结构化证据包。
  2. 加载 `playbooks/<剧本名>.md`，严格按其"步骤"执行、按其"证据包字段"输出。
  3. 风险类：加载 `core/playbooks/风险扫描.md`；透镜以**裸名**传入，路径经 `core/lenses/_index.md` 登记表解析（透镜按 DFX 维度分目录，如 `core/lenses/可靠性/资源泄漏.md`），未登记则 Glob 兜底，仍无则 partial 停步。以透镜"代码特征模式"为扫描指令。
  4. 溯源铁律 + 证据包纪律（见壳内容）。
  5. 不做结论性测试建议（那是族 agent 的职责）——只交事实证据。

#### 2.3.2 mr-reader（M1 交付接口壳，内部实现留内网）

- **mode**：`subagent`。
- **定位**：收编用户已有 `mr_reader` skill，负责拉取/解析 MR，产出"MR 证据包"。
- **接口契约**：
  - 输入：MR 链接（触发 MCP 泛化探测，R-7.5）**或** 用户粘贴的 diff + MR 描述。
  - 输出：MR 证据包（schema 见 §4.2）。
  - MCP 探测：探测 codehub 类 MCP 工具→用其拉 MR；无则请用户粘贴。**不硬编码工具名。**
- **速度型例外【推测，按铁律精神裁定】**：MR 获取**一律经 mr-reader**（调用轻量、产出即压缩证据包），避免探测/拉取逻辑在族 agent 内再实现一份而漂移。R-7.6"速度型不走能力 subagent"限缩解释为：不走 code-excavator、不落 `runs/` 工件；速度型下 `mr_summary` 内联消费、不落盘。
- **内部实现**：`<!-- 待迁移：内网 mr_reader 原文件 -->`

#### 2.3.3 log-miner（M2 交付）

- **mode**：`subagent`。定位：大日志挖掘（grep + 时间线重建），产出"日志/报文摘要"证据包（schema 见 §4.2）。输入：日志片段或路径（文件可能很大，自行 grep，R-8.3）。
- **提示词纲要**（M2 落地，先立骨架）：只读日志（grep 需 bash 读权限，权限收紧幅度随 T-3 实测定）；按时间戳重建 `timeline`；错误码/告警/复位/重传等关键信号入 `key_signals`；原文片段进 `raw_excerpts` **禁止改写**；事件关联标【推测】入 `correlations`；**只交事实，不下根因结论**（根因是 troubleshooter 的职责）。

#### 2.3.4 pcap-analyzer（M3 交付）

- **mode**：`subagent`。定位：抓包分析（协议报文时序、异常报文），产出报文摘要证据包。
- **提示词纲要**（M3 落地，先立骨架）：按协议（NVMe/TCP PDU、iSCSI PDU…）解析报文时序入 `timeline`；异常报文（乱序/重传/非法字段/协议违例）入 `key_signals`；与日志时间线的对齐线索标【推测】入 `correlations`；只交报文事实，不下根因结论。

#### 2.3.5 auditor（M1 交付壳；2026-07-24 评审裁定由 M2 提前）

- **mode**：`subagent`（`permission: edit: deny`，只审不改）。
- **入选理由**：能力 subagent 四判据中"需要独立视角"这一条的唯一持有者——必须与产出方**上下文隔离**，才能真正独立复核。
- **定位**：独立 Judge（迁移自 Codetalks 独立 Judge）。深度模式收尾时复核：溯源是否达标、黑盒可执行性是否达标、覆盖是否完整（覆盖审计 / Coverage Gate）。
- **输出**：auditor 审查意见（schema 见 §4.3）。
- **交付说明**：M1 交付薄壳 + §4.3 schema + 检查清单骨架，使 M1 深度收尾门闭环；Codetalks 独立 Judge 与 Coverage Gate 的原版提示词/机制回内网核对补齐（待办 M-8/M-9）。
- **提示词纲要**：
  1. 你是独立审计者，与产出方上下文隔离；只审不改。
  2. 输入：被审产出 + 其 `runs/` 证据包；按 §4.3 四组 checks 逐项核查，每条违规给位置 + 依据。
  3. 裁定聚合规则见 §4.3；FAIL/CONCERNS 时产出**结构化** `required_actions`（可直接机器映射回剧本重挖调用）。

---

## 3. 挖掘剧本清单全稿

> 在 handover §3 初稿基础上**细化到全稿粒度**（P1–P9 与初稿九项一一对应），并**新增 2 个候选剧本**（P10/P11，【推测】交待办 A-5 审）。每个剧本一段：**目标 / 步骤 / 证据包字段**。证据包字段即该剧本专属的 schema（配合 §4.1 公共外层）。清单交用户审、执行时可扩展。**M1 交付 ★ 项 = 结构类 5 个 + 风险扫描 P0（"5+1"，与 R-11.1 对齐）**。
>
> **规范调用名（= 文件名）**：调用契约、证据包 `playbook` 字段、manifest 登记**一律用下表规范名**，禁止用全名派发（否则 excavator 按名加载找不到文件）：
>
> | 剧本 | 规范调用名/文件名 | 剧本 | 规范调用名/文件名 |
> |---|---|---|---|
> | P0 风险扫描 | `风险扫描` | P6 调用链与影响域 | `调用链影响域` |
> | P1 入口与主干追踪 | `主干追踪` | P7 并发上下文识别 | `并发上下文识别` |
> | P2 分支全枚举 | `分支枚举` | P8 超时与重试点盘点 | `超时重试盘点` |
> | P3 状态机提取 | `状态机提取` | P9 协议报文收发路径 | `协议报文收发路径` |
> | P4 资源生命周期配对 | `资源生命周期` | P10 初始化与卸载时序 | `初始化卸载时序` |
> | P5 异常传播路径 | `异常传播` | P11 配置与规格参数盘点 | `配置规格盘点` |
>
> **升格出口**（handover §3）：实测表现差的剧本允许升格为独立 agent，架构允许两种形态共存。

### 结构类剧本（挖"是什么"）

**P1 入口与主干追踪 ★**
- 目标：找到对象的所有入口点，追出正常路径主干调用链。
- 步骤：① 定位入口（注册回调 / 系统调用 / 报文分发表 / CLI 命令表）；② 沿正常路径追主干函数序列；③ 标注每跳 `文件:行号`；④ 标出与外部可观测点（报文/CLI/日志/告警）的接触面。
- 证据包字段：`entries[]`（入口点 {符号, 位置, 触发条件}）、`main_chain[]`（有序调用 {函数, 位置, 作用}）、`observable_touchpoints[]`（外部可观测接触点 {类型, 位置, 现象}）。

**P2 分支全枚举 ★**
- 目标：穷举对象内所有条件分支与其进入条件。
- 步骤：① 遍历 if/switch/error-check/能力协商分支；② 每分支记录进入条件 + 走向 + 是否有外部可观测差异；③ 标注难以从外部触发的分支（供族 agent 判黑盒可达性）。
- 证据包字段：`branches[]`（{条件, 位置, 分支去向, 外部可触发性【推测标注】, 可观测差异}）。

**P3 状态机提取 ★**
- 目标：提取对象的状态集合、状态迁移及触发事件。
- 步骤：① 找状态变量/枚举；② 列全状态；③ 列迁移（源态→事件→目标态 + 动作）；④ 标非法迁移处理；⑤ 关联外部触发手段与可观测状态映射（存储特色：如连接状态机、iSCSI 会话态）。
- 证据包字段：`states[]`、`transitions[]`（{from, event, to, action, 位置}）、`illegal_transition_handling[]`、`external_trigger_map[]`（状态↔外部手段/观测）。

**P4 资源生命周期配对 ★**
- 目标：配对资源的申请/释放，找生命周期缺口。
- 步骤：① 列资源（内存/连接/队列/引用计数/锁/文件描述符）；② 配对 alloc↔free、get↔put、lock↔unlock；③ 标未配对/异常路径漏释放/双重释放点。
- 证据包字段：`resources[]`（{资源, alloc 位置, free 位置, 配对状态, 异常路径缺口【推测】}）。

**P5 异常传播路径 ★**
- 目标：追踪错误码/异常从产生到最终处置的传播链。
- 步骤：① 找错误产生点（返回码/errno/异常）；② 追传播（层层返回、转译、吞掉）；③ 找最终处置（重试/回滚/上报/静默）；④ 标外部可观测的错误表现（错误码/告警/日志）。
- 证据包字段：`error_origins[]`、`propagation_chains[]`（{origin→…→sink, 每跳位置, 是否转译/吞掉}）、`observable_error_surface[]`。

**P6 调用链与影响域**
- 目标：给定改动点/函数，算出上游调用者与下游被影响面（回归影响分析，MR 分析常用）。
- 步骤：① 定位目标符号；② 反向找 caller 树；③ 正向找 callee 与共享数据影响；④ 归纳受影响的外部特性。
- 证据包字段：`target`、`callers[]`、`callees[]`、`shared_state_impact[]`、`affected_external_features[]`。

**P7 并发上下文识别**
- 目标：识别共享数据、锁边界、执行流（存储底软并发密集）。
- 步骤：① 列执行流（中断/软中断/内核线程/工作队列/用户态线程）；② 列共享数据及其保护锁；③ 标锁边界与临界区；④ 标无锁/疑似竞争点。
- 证据包字段：`execution_contexts[]`、`shared_data[]`（{数据, 保护锁, 访问上下文}）、`lock_boundaries[]`、`suspected_races[]`【推测】。

**P8 超时与重试点盘点**
- 目标：盘点所有超时/重试/退避机制及其参数来源。
- 步骤：① 找定时器/超时值/重试计数/退避；② 记录参数来源（硬编码/配置/协商）；③ 标超时触发后的动作与可观测现象。
- 证据包字段：`timeouts[]`（{位置, 时长来源, 触发动作, 可观测现象}）、`retries[]`（{位置, 次数, 退避策略, 上限行为}）。

**P9 协议报文收发路径（存储协议特色）**
- 目标：从 socket 收包到业务处理的全链路（NVMe/TCP、iSCSI 等）。
- 步骤：① 定位收包点（socket recv / DMA / 队列取）；② 追解析（PDU/胶囊/CmdSN 校验）；③ 追分发到业务处理；④ 追发包路径与响应构造；⑤ 标每段的错误/异常报文处理与可观测点。
- 证据包字段：`rx_path[]`、`parse_steps[]`（{字段校验, 位置}）、`dispatch[]`、`tx_path[]`、`malformed_handling[]`、`observable_wire_behavior[]`。

**P10 初始化与卸载时序（新增候选，【推测】交 A-5 审）**
- 目标：模块/设备/连接的初始化与拆除全序列，找顺序依赖与拆除遗漏（存储底软 probe/remove、建链/断链高发区）。
- 步骤：① 追 init/probe/建链序列（资源就绪顺序）；② 追 exit/remove/断链序列；③ 比对两序列对称性（init 了未 deinit 的项）；④ 检查中途失败的回滚路径完整性。
- 证据包字段：`init_sequence[]`、`teardown_sequence[]`、`asymmetries[]`（不对称项）、`rollback_gaps[]`【推测】。

**P11 配置与规格参数盘点（新增候选，【推测】交 A-5 审）**
- 目标：盘点可配置项与规格上限（队列深度、连接数、超时值、批量上限）及超限行为。
- 步骤：① 找配置读取点与默认值；② 找规格常量与上限校验；③ 标超限行为（拒绝/截断/未校验）；④ 关联外部配置手段（CLI/配置文件/协议协商）。
- 证据包字段：`config_items[]`（{项, 默认值, 来源, 位置}）、`spec_limits[]`（{上限, 校验位置, 超限行为}）、`unvalidated_inputs[]`【推测】。

### 风险类（不写独立剧本，共用"风险扫描"流程 × 透镜）

**P0 风险扫描（通用流程）★**
- 目标：以某个 DFX 透镜为镜片，扫描对象内匹配"代码特征模式"的风险点。
- 步骤：① 按裸名经 `core/lenses/_index.md` 解析透镜路径并加载（Glob 兜底，缺失则 partial 停步）；② 以其"代码特征模式"栏作为 grep/阅读指令扫描对象；③ 命中点逐一记录证据 + 匹配的机理；④ 未命中给"免疫理由"（供全透镜浅扫模式一句话说明）。
- 证据包字段：`lens`、`hits[]`（{位置, 命中的特征模式, 风险机理, 可能失效模式}）、`immunity_note`（无命中时）。

---

## 4. 交接工件 schema（起草，交用户审）

> 所有交接工件是 **YAML/JSON 结构 + 必要 Markdown 叙述** 的混合：结构部分供 agent 机器消费与断点恢复，叙述部分供人读。以下为**起草稿**，字段可增删，交用户审定后固化到 `shared/证据包schema.md`。
> 评审修订说明：handover 任务 B 原要求"三种工件 schema"（代码证据包 / 日志·报文摘要 / auditor 意见）；评审发现 manifest 作为断点恢复索引同样是机器消费契约，补为第四种（§4.4），并附 M1 模板最小字段清单（§4.5）。

### 4.1 交接工件 1：代码证据包（分剧本变体）

**公共外层（所有剧本共用）**：

```yaml
artifact_type: code_evidence
schema_version: 0.1
playbook: <剧本名>              # 如 主干追踪 / 风险扫描
target: <挖掘对象>              # 如 nvmet_tcp_recv
lens: <透镜名 | null>          # 仅风险扫描填
source_ref:                    # 溯源根
  repo_or_path: <源码路径>
  commit_or_mr: <可选>
status: complete | partial     # partial 支持断点恢复
progress:                      # 断点续挖的结构化依据（与剧本"步骤"编号对应）
  done_steps: []               # 已完成步骤号，如 [1, 2]
  pending_steps: []            # 未完成步骤号
  resume_hint:                 # 续挖起点
    file: <续挖文件>
    symbol: <续挖符号>
    note: <从哪继续的一句话>
coverage_note: <本次覆盖/遗漏说明（人读补充；机器续挖以 progress 为准）>
findings: <剧本专属字段，见 §3 每剧本"证据包字段">
inferences:                    # 溯源双轨制：推断单独列
  - claim: <推测内容>
    basis: <依据>
    verify_method: <黑盒验证方法>
open_questions: []             # 需族 agent 或用户回答
```

- **剧本变体** = 上面 `findings` 换成 §3 对应剧本的"证据包字段"。例如 `playbook: 主干追踪` 时 `findings: {entries, main_chain, observable_touchpoints}`。
- **溯源纪律落到字段**：`findings` 内每个条目必须带 `位置: 文件:行号`；无法给出行号的内容必须移到 `inferences[]`。

### 4.2 交接工件 2：日志/报文摘要 与 MR 摘要

> 评审修订：`mr_summary` 从 log/pcap 共用外层中**拆出独立 schema**——`timeline`/`key_signals` 对 MR 无自然语义，混用导致字段污染。真正共用的只有 `source_ref` 与 `raw_excerpts`。

**4.2a `log_summary` / `pcap_summary`（log-miner / pcap-analyzer）**：

```yaml
artifact_type: log_summary | pcap_summary
schema_version: 0.1
source_ref:
  path_or_link: <日志路径 / pcap 路径>
  range: <行号区间 / 时间区间 / 报文序号区间>
timeline:                      # 按时间重建的事件线（定位核心）
  - ts: <时间戳>
    event: <事件>
    raw_ref: <原文位置，如 log:12345 / pkt:88>
    note: <解读>
key_signals: []                # 错误码/告警/异常报文/重传等关键信号
correlations: []               # 事件间关联【推测标注】
raw_excerpts: []               # 关键原文片段（溯源用，禁止改写）
```

**4.2b `mr_summary`（mr-reader）**：

```yaml
artifact_type: mr_summary
schema_version: 0.1
source_ref:
  path_or_link: <MR 链接 | "用户粘贴">
mr:
  title: <MR 标题>
  description: <MR 描述原文摘要>
  changed_files: []            # {file, hunks 概述}
  change_intent: <改动意图>
  risk_hotspots: []            # {位置, 疑点}，供 dev-expert 决定挖哪些剧本
raw_excerpts: []               # 关键 diff/描述原文片段（溯源用，禁止改写）
```

> mr-reader 的内部拉取实现留占位；本 schema 是**对外契约**，M1 即固定，内网实现只需产出符合本 schema 的结果。

### 4.3 交接工件 3：auditor 审查意见

```yaml
artifact_type: audit_opinion
schema_version: 0.1
audited_artifact: <被审产出的标识/路径>
verdict: PASS | CONCERNS | FAIL
checks:
  traceability:                # R-7.1 溯源双轨制
    verdict: PASS | FAIL
    violations: []             # {location, issue：无行号的事实 / 未标推测}
  blackbox_executability:      # R-7.2 黑盒可执行性
    verdict: PASS | FAIL
    violations: []             # {case_id, issue：出现黑盒做不到的步骤}
  coverage:                    # 覆盖审计 / Coverage Gate
    verdict: PASS | CONCERNS | FAIL
    dimension: <全透镜浅扫 | 分支覆盖 | 状态覆盖 …>
    gaps: []                   # 未覆盖项 + 原因
  format_compliance:           # R-7.3 格式不擅改
    verdict: PASS | FAIL
    violations: []
required_actions:              # FAIL/CONCERNS 时的结构化补救清单（可机器映射回重挖调用）
  - action_type: re_excavate | fix_format | add_evidence | rewrite_case
    playbook: <规范剧本名 | null>    # re_excavate 时必填（§3 规范名）
    target: <挖掘对象 | null>
    lens: <透镜名 | null>
    reason: <一句话原因>
    ref_violation: <指向上方 checks.*.violations 的条目>
```

**裁定聚合规则**：任一子项 FAIL ⇒ 总 verdict FAIL；无 FAIL 但 coverage 为 CONCERNS ⇒ 总 CONCERNS；全 PASS ⇒ PASS。`blackbox_executability.violations` 中的 `case_id` 引用黑盒用例模板的"用例编号"字段（模板必含此字段，见 §4.5）。

---

### 4.4 交接工件 4：`runs/` 任务清单 manifest（断点恢复索引）

> 评审修订：manifest 是恢复协议（§6.2）第一步要读的核心索引，补齐 schema。**字段永远限于本次深度任务的工件状态**——往里加"用例执行进度"之类字段即越界为被否决的"项目状态文件"（R-12），禁止。

```yaml
artifact_type: run_manifest
schema_version: 0.1
task_id: <任务id，生成规则见 shared/调度规则.md>
scenario: <场景 skill 文件名>
target: <分析对象>
mode: deep                     # 速度型不落 runs/
inputs_ref: []                 # 输入来源（MR 链接 / 源码路径 / mr_summary 工件文件名）
planned_artifacts:             # 本次计划产出的全部工件（含 mr_summary 等非挖掘工件）
  - artifact_type: code_evidence | mr_summary | log_summary | pcap_summary
    playbook: <规范剧本名 | null>   # code_evidence 必填
    target: <对象>
    lens: <透镜 | null>
    status: pending | partial | complete
    artifact_file: <runs/<id>/ 下的文件名 | null>
summary_status: pending | partial | complete   # 汇总阶段状态
audit:
  rounds: 0                    # 已回挖轮数（上限 2，见 §6.2 终止条件）
  status: pending | PASS | CONCERNS | FAIL
  opinion_file: <audit_opinion 工件文件名 | null>
```

### 4.5 M1 四模板最小字段清单（templates/，R-7.3 的格式载体）

> 评审修订：模板是 M1 交付物但此前无内容规格，补最小字段清单。通用版仅为占位骨架，团队版内网替换（M-5）后即受 R-7.3 保护、不得擅改。

- **黑盒用例.md（通用占位版）**，每条用例必含：`用例编号`（auditor `case_id` 的引用锚点）/ `用例名称` / `预置条件` / `外部触发手段`（R-7.2）/ `操作步骤` / `观测手段`（从 `shared/观测手段目录.md` 选取）/ `观测判据（PASS/FAIL 判定）` / `推导方法论`（引 methods/，可追溯）/ `关联风险`（引 lenses/ 或 SFMEA 条目，可选）。
- **SFMEA.md**，每行必含：`条目编号` / `失效模式` / `失效机理`（引透镜）/ `代码证据（文件:行号）` / `影响` / `严重度·发生度·探测度` / `外部可观测表现` / `建议测试手段`。
- **报告-模块全量分析.md** 章节骨架：分析对象与输入 → 主干与分支结构（证据包引用）→ 状态机与资源生命周期 → 异常传播 → SFMEA（全透镜浅扫结果，含免疫说明；M1 为种子透镜范围）→ 黑盒测试场景 → 用例清单 → 覆盖审计结论（auditor）→ 待用户确认（知识回填询问，R-7.4）。
- **报告-MR问题单分析.md** 章节骨架：MR 摘要（mr_summary 引用）→ 改动影响域（调用链）→ 回归风险点 → 针对性用例 → 审计结论 → 待用户确认。

---

## 5. opencode 适配写法（查证后）+ Claude Code 预留

> 本节格式**已联网查证** opencode 官方文档（opencode.ai/docs/agents、/docs/skills）。**注意版本风险**：agent 目录单复数在不同来源存在冲突（见【待确认】），M1 落地前须对齐 codeagent 实际版本。

### 5.1 opencode agent 文件格式（查证结果）

- **位置**：项目级 `.opencode/agents/<name>.md`，全局 `~/.config/opencode/agents/<name>.md`。
  - **【待确认】**：官方 docs 页显示复数 `agents/`；部分社区文档使用单数 `.opencode/agent/`。codeagent 为 opencode 改造版，须以其实际版本为准。M1 建仓时先跑一次最小 agent 验证目录名，再批量落地。（见内网待办 T-1）
- **文件名 = agent 标识**（如 `dev-expert.md` → agent 名 `dev-expert`）。
- **结构**：YAML frontmatter（配置）+ Markdown 正文（system prompt）。
- **frontmatter 字段**（查证）：

  | 字段 | 取值 | 用途 |
  |---|---|---|
  | `description` | 字符串（必填）| agent 用途简述，Dispatcher/Task 据此自动选用 |
  | `mode` | `primary` / `subagent` / `all` | 调用方式；不填默认 `all` |
  | `model` | `provider/model-id` | 覆盖默认模型 |
  | `temperature` | 0.0–1.0 | 随机性 |
  | `top_p` | 0.0–1.0 | 随机性（备选）|
  | `permission` | 对象 | 细粒度权限（如 `edit: deny`、`bash: deny`）|
  | `color` | 颜色 | UI 外观 |
  | `disable` | 布尔 | 禁用 |
  | `hidden` | 布尔 | 从 `@` 自动补全隐藏（仅程序化调用）|

- **调用方式**：手动 `@name`；主 agent 经 **Task 工具**按 description 自动调用。
- **mode 选型（本系统）**：
  - Dispatcher → `primary`。
  - 3 族 agent → `all`【推测】：既要能被用户 Tab 切换为主对话，又要能被 Dispatcher 经 Task 调用。若 codeagent 语义不同，退化为 `primary` + 用户显式进入。
  - 5 能力 subagent → `subagent`；其中 code-excavator/log-miner 建议 `permission: { edit: deny, bash: deny }`（只读挖掘）。auditor 同样 `edit: deny`。

- **示例（code-excavator 壳，M1 落地样例）**：

```yaml
---
description: 只读代码考古者；按注入的挖掘剧本产出结构化代码证据包（溯源到 文件:行号）
mode: subagent
temperature: 0.1
permission:
  edit: deny
  bash: deny
---
# 你是 code-excavator
（正文引用 shared/溯源铁律.md 与 playbooks/<剧本>.md —— 见架构 §2.3.1 提示词纲要）
```

### 5.2 opencode skill 文件格式（查证结果）

- **位置**：`.opencode/skills/<name>/SKILL.md`（项目级）、`~/.config/opencode/skills/<name>/SKILL.md`（全局）；opencode 亦兼容读取 `.claude/skills/<name>/SKILL.md` 与 `.agents/skills/<name>/SKILL.md`。
- **frontmatter**：`name`（必填，1–64 字符，小写字母数字+单连字符）、`description`（必填，1–1024 字符）、可选 `license` / `compatibility` / `metadata`。
- **正文**：描述"What I do / When to use me"，供 agent 决定是否加载。
- **发现与调用**：opencode 从当前目录向上走到 git worktree 自动发现；agent 通过原生 `skill` 工具看到名称+描述，`skill({ name: '<name>' })` 加载。
- **本系统 scenarios/ 的落地映射【推测，交用户审】**：opencode 的"skill"是"文件夹 + SKILL.md"的能力包，与本系统"scenarios/ 是纯 md 作业流程"概念相近但不完全等价。两种落地路线：
  - **路线 A（推荐）**：`scenarios/*` 保持为**平台无关纯 md 知识文件**放在 `core/scenarios/`，由族 agent 正文用相对路径引用/读取。opencode `skill` 机制仅用于确实需要"按需加载 + 自动发现"的能力（如把某个大场景包成一个 opencode skill 放 `adapters/opencode/skills/`）。
  - **路线 B**：把每个 scenario 直接做成 opencode skill（`SKILL.md`）。缺点：耦合 opencode 目录结构、伤害平台无关性。
  - 决策：**默认路线 A**，保住"core 平台无关、adapters 薄壳"（R-5.1/R-9.4）。此点请用户确认（内网待办 T-2）。

### 5.3 Claude Code 适配预留

- 目录预留 `adapters/claude-code/`（M1 建空壳 + README 说明）。
- 映射规则（后补，M1 不实现）：
  - opencode agent（`.opencode/agents/*.md`）→ Claude Code `.claude/agents/*.md`（frontmatter 字段名有差异，届时按 Claude Code 文档重写薄壳）。
  - opencode skill / 本系统 scenarios → Claude Code `.claude/skills/<name>/SKILL.md`。
  - `core/` 纯 md 资产**零改动**跨平台复用——这正是分层设计的收益点。

---

## 6. 深度模式并行 fan-out 与断点恢复机制

### 6.1 并行 fan-out 实现方式

**标准形态**：族 agent（深度模式）把一次分析拆成多个**独立挖掘子任务**，各自派一个 code-excavator 实例（注入不同剧本/透镜），**并行**执行、各自独立上下文，回收证据包后由族 agent 汇总。

- **示例（dev-expert 全量分析）**：并行派发 4 实例——
  - `code-excavator(模块X, 主干追踪)`
  - `code-excavator(模块X, 异常传播)`
  - `code-excavator(模块X, 资源生命周期)`
  - `code-excavator(模块X, 风险扫描, 透镜=并发)`
  - 4 个证据包回收后，dev-expert 汇总为 SFMEA + 场景 + 用例。

- **载体实现**：
  - **codeagent/opencode**：族 agent 在一条消息内发起多个 **Task 工具**调用（每个指向 code-excavator，参数带剧本名/对象/透镜）。opencode 主 agent 可并发调度 subagent；各 subagent 上下文隔离，返回值即证据包文本。**【待确认】codeagent 的并发上限与返回体量限制**（内网待办 T-3）——超限时降级为分批串行，语义不变。
  - **Claude Code（后补）**：等价用 Task 工具并发派 subagent。

- **为什么并行**：① 各剧本上下文隔离，互不污染；② 挖掘是脏活层最耗时环节，并行显著降墙钟时间；③ 证据包是压缩产出，汇总层上下文可控。

### 6.2 断点恢复机制

深度模式"每步落交接工件"既是审计需要，也是断点恢复的基础（迁移自 Codetalks"深度型分步保存工件 + compact 恢复"）。

- **工件即断点**：每个能力 subagent 的工件均**由族 agent 写入** `runs/<任务id>/`——subagent 只读、只回传证据包文本（excavator edit/bash 双 deny 写不了盘）；目录创建、manifest 创建与更新、工件写盘**全部是族 agent 的职责**（不限 code-excavator，含 mr_summary / log_summary 等）。文件名编码 `{场景}-{对象slug}-{剧本或artifact_type}-{序号}.md`。**对象 slug 规则**：仅取符号名/短名，`/`、`:`、空格一律替换为 `__`（避免路径分隔符入文件名）。工件外层 `status: complete|partial` 标识是否完成。
- **恢复协议**：
  1. 族 agent 启动深度任务时，先查 `runs/<任务id>/`。**目录已存在时先向用户确认：续跑（恢复）还是全新重挖（新建 id，原 id 追加 `-2` 后缀递增）**——防止"重跑"被静默偷换成"恢复"。
  2. 读 `manifest.md`（schema 见 §4.4）；`status: complete` 的工件→**跳过重挖**，直接载入。
  3. `status: partial` 的→按其 `progress.pending_steps` 与 `progress.resume_hint` 结构化续挖（`coverage_note` 仅供人读参考）。
  4. 全部工件 complete→按 manifest 的 `summary_status`/`audit.status` 决定从汇总还是审计续跑。
- **任务 id**：生成规则统一落 `shared/调度规则.md`——Dispatcher 生成；用户绕过 Dispatcher 直接进入族 agent 时，由族 agent 按**同一规则**自举生成（§2.2.1 自举协议）。【推测：`{场景}-{对象slug}-{日期}`，日期由运行环境提供（T-5）】。
- **与 compact/上下文丢失的关系**：即便主对话上下文被压缩或中断，`runs/` 下的工件是**外部持久状态**，重进族 agent 读 `manifest.md` 即可无损续跑。
- **收尾门**：所有工件 complete → 汇总 → auditor 复核（§4.3）→ 覆盖审计 PASS 才算深度任务完成；FAIL/CONCERNS 则按结构化 `required_actions` 回挖对应剧本（生成新工件，manifest `audit.rounds` +1）。**终止条件：最多回挖 2 轮**，仍未 PASS 则带 CONCERNS 出报告、首页标注未决项，交用户裁决——避免审计死循环。

---

## 7. 目录规范全树

> 顶层遵循 R-9.6：`core/`（平台无关）+ `adapters/`（各平台壳）+ `docs/`。M1 只落 ★ 项，其余建目录 + `.gitkeep`/README 占位。

```
pangea-test/
├─ docs/
│  ├─ requirements.md ★
│  ├─ architecture.md ★（本文）
│  └─ 内网待办清单.md ★（M1 时从本文附录抽出成独立文件）
│
├─ core/                       # 平台无关纯 md 资产（跨载体复用）
│  ├─ shared/                  # 全局铁律与纲领（所有 agent 引用）
│  │  ├─ 溯源铁律.md ★
│  │  ├─ 铁律总纲.md ★         # R-7.3~R-7.7
│  │  ├─ 八问纲领.md ★         # 迁移自 Codetalks，M1 搭框架留占位
│  │  ├─ 观测手段目录.md ★     # 实体文件（R-6.4 双重身份）；M1 骨架：按 报文/CLI回显/告警/日志/性能统计/错误码 分类的手段条目，M3 与可服务性透镜合流
│  │  ├─ 调度规则.md ★         # 模式判定判据 + 任务id生成规则（Dispatcher 与族 agent 共同引用，只存一份）
│  │  └─ 证据包schema.md ★     # §4 四工件 schema 定稿后落此
│  │
│  ├─ scenarios/               # 场景作业流程（加场景=加md）
│  │  ├─ 模块全量分析.md ★     # 九步全链路，M1 骨架+迁移占位
│  │  ├─ MR问题单分析.md ★     # 九步裁剪版，M1 骨架+迁移占位
│  │  ├─ FST逃逸复盘.md
│  │  ├─ 共性问题排查.md
│  │  ├─ 专项风险分析.md
│  │  ├─ 原理讲解.md
│  │  ├─ 日志定位.md           # M2
│  │  ├─ 失败用例三分类.md     # M2
│  │  ├─ 抓包辅助定位.md       # M3
│  │  ├─ 可测试性分析.md       # M3
│  │  ├─ 测试策略.md           # M3
│  │  ├─ 用例评审.md           # M3
│  │  └─ 缺陷单撰写.md         # M3
│  │
│  ├─ playbooks/               # 挖掘剧本库（加挖掘=加md）
│  │  ├─ 主干追踪.md ★
│  │  ├─ 分支枚举.md ★
│  │  ├─ 状态机提取.md ★
│  │  ├─ 资源生命周期.md ★
│  │  ├─ 异常传播.md ★
│  │  ├─ 风险扫描.md ★         # 通用流程，配合 lenses/
│  │  ├─ 调用链影响域.md
│  │  ├─ 并发上下文识别.md
│  │  ├─ 超时重试盘点.md
│  │  ├─ 协议报文收发路径.md
│  │  ├─ 初始化卸载时序.md     # 新增候选（A-5 待审）
│  │  └─ 配置规格盘点.md       # 新增候选（A-5 待审）
│  │
│  ├─ lenses/                  # DFX 风险透镜库（找什么风险）；M1 种子 3 个，M3 全量初稿
│  │  ├─ _index.md ★           # 按 DFX 维度归类索引（M1 只登记种子）
│  │  ├─ 可靠性/
│  │  │  ├─ 资源泄漏.md ★      # M1 种子透镜
│  │  │  ├─ 并发.md ★          # M1 种子透镜
│  │  │  ├─ 超时恢复.md ★      # M1 种子透镜
│  │  │  └─ …                  # M3 全量
│  │  ├─ 可用性/… 性能/… 规格/… 韧性/… 升级/…   # M3 全量
│  │  └─ 可服务性.md ★         # 引用桩（M1 已建）：指向 core/shared/观测手段目录.md 实体（R-6.4 单一事实源，防两文件漂移）
│  │
│  ├─ methods/                 # 测试设计方法论库（怎么推用例）；M1 种子 2 个，M3 全量初稿
│  │  ├─ _selector.md ★        # 测试点特征→适用方法（M1 骨架版，只含种子两法）
│  │  ├─ 状态转换.md ★         # M1 种子方法论
│  │  ├─ 边界值分析.md ★       # M1 种子方法论
│  │  └─ <其余 10 方法论>.md   # M3
│  │
│  ├─ templates/               # 输出模板（格式不擅改，R-7.3）
│  │  ├─ 黑盒用例.md ★         # 通用版，标注"占位，内网替换团队版"
│  │  ├─ SFMEA.md ★            # 通用格式
│  │  ├─ 报告-模块全量分析.md ★
│  │  └─ 报告-MR问题单分析.md ★
│  │
│  ├─ protocols/               # 协议领域知识（NVMe/TCP、iSCSI、NOF、KV、XNET、XRT…）
│  │  └─ _index.md             # 知识优先级 R-7.4 的知识文件落此，随用随填
│  │
│  └─ modules/                 # 模块领域知识（随分析回填）
│     └─ _index.md
│
├─ adapters/                   # 各平台薄壳（只声明加载 core/ 资产）
│  ├─ opencode/
│  │  └─ .opencode/
│  │     ├─ agents/            # 【待确认】单复数，见 §5.1
│  │     │  ├─ dispatcher.md ★
│  │     │  ├─ dev-expert.md ★
│  │     │  ├─ troubleshooter.md   # M2
│  │     │  ├─ test-designer.md    # M3
│  │     │  ├─ code-excavator.md ★
│  │     │  ├─ mr-reader.md ★      # 接口壳，实现留内网
│  │     │  ├─ log-miner.md        # M2
│  │     │  ├─ pcap-analyzer.md    # M3
│  │     │  └─ auditor.md ★        # M1 壳（评审裁定提前）；Judge 提示词内网补（M-9）
│  │     └─ skills/            # 仅在采用 §5.2 路线 A 的按需能力时使用
│  │
│  └─ claude-code/             # 预留空壳 + README（后补）
│     └─ README.md ★
│
└─ runs/                       # 深度模式交接工件 / 断点（§6.2），非资产、可 gitignore
   └─ <任务id>/
      ├─ manifest.md
      └─ <场景>-<对象>-<剧本>-<序号>.md
```

> **注**：`adapters/opencode/.opencode/…` 的嵌套是为了让"仓库内 adapters 目录"与"运行时 `.opencode` 约定目录"共存；实际部署时可用符号链接或构建脚本把 `adapters/opencode/.opencode` 映射到仓根 `.opencode`。此部署细节 M1 验证时确定（内网待办 T-1）。

---

## 8. 双模式执行时序

**速度型**（族 agent 内联、不落工件）：

```
用户 → Dispatcher（路由+判定速度型）→ dev-expert
  dev-expert 内联读码/读知识 → 直接产出讲解/单点分析（Markdown）
  （MR 类速度型例外：MR 获取仍经 mr-reader，mr_summary 内联消费不落盘，§2.3.2）
  → Dispatcher 场景衔接推荐下一步
```

**深度型**（走能力层、落工件、过 auditor）：

```
用户 → Dispatcher（路由+判定深度型，生成任务id）→ dev-expert
  dev-expert 读 runs/<id>/manifest（断点恢复）
   → 并行 fan-out：Task→code-excavator ×N（各注入剧本/透镜，只读）
       每个回传 code_evidence 工件 → 落 runs/<id>/
   → dev-expert 汇总 → SFMEA + 场景 + 黑盒用例（用 methods/ 推导、lenses/ 定风险）
   → Task→auditor（独立上下文）复核 → audit_opinion
       PASS → 完成；FAIL/CONCERNS → 按结构化 required_actions 回挖（≤2 轮，§6.2）
   → Dispatcher 场景衔接推荐下一步（回填询问已写入报告"待用户确认"节）
```

**深度型·MR/问题单分析变体**（M1 第二场景，mr-reader 前置）：

```
Dispatcher（前置收集：MR 链接或粘贴 diff；生成任务id）→ dev-expert
  → Task→mr-reader → mr_summary 工件落 runs/<id>/（manifest 登记 artifact_type=mr_summary）
  → dev-expert 按 mr.risk_hotspots 选剧本（常用：调用链影响域[M3；M1 以主干追踪+分支枚举替代] / 异常传播 / 风险扫描×透镜）
  → fan-out code-excavator ×N → 汇总回归风险点 + 针对性用例 → auditor → 报告
```

---

## 附. 内网待办清单

> 本清单汇总所有"需在内网 codeagent 环境完成"的占位点，供用户带回逐项处理。M1 开工时建议抽成独立文件 `docs/内网待办清单.md`。
>
> **已裁定（2026-07-24 评审门）**：① auditor 壳提前至 M1（原 M2）；② M1 附带种子两库（3 透镜 + 2 方法论 + `_selector.md` 骨架）与风险扫描剧本（"5+1"）。相应修订见 R-11.1/R-11.2、§2.3.5、§3、§7。

**迁移类（需 Codetalks / 内部资产原文件）**：
- **M-1**：`shared/八问纲领.md` —— 迁移 Codetalks 八问分析纲领全文（干什么/怎么触发/正常流程/分支进入/状态资源变化/异常传播/潜伏累积并发/黑盒怎么构造观察）。M1 只搭骨架。
- **M-2**：`scenarios/模块全量分析.md` —— 逐字继承改造 Codetalks 九步全链路（入口识别→主流程分支状态资源异常传播→风险SFMEA→黑盒场景→黑盒流程→黑盒用例→覆盖审计）。**逐字继承而非重写。**
- **M-3**：`scenarios/MR问题单分析.md` —— 九步链路的裁剪版，继承自 Codetalks。
- **M-4**：`scenarios/日志定位.md` —— 迁移 Codetalks"问题根因辅助定位"（时间线→异常传播链→候选根因）。（M2）
- **M-5**：`templates/黑盒用例.md` —— 用团队版**替换** M1 通用占位版；替换后受铁律 R-7.3 保护，不得擅改。
- **M-6**：`mr-reader` 内部实现 —— 迁移用户已有 `mr_reader` skill；须产出符合 §4.2 `mr_summary` schema 的结果。
- **M-7a**：`scenarios/专项风险分析.md` —— 迁移 Codetalks 专项风险分析流程；其 8 个风险主题（泄漏/耗尽/计数翻转/并发/超时恢复/协议安全/性能退化/HA）逐一映射为透镜库条目（M1 种子已覆盖 泄漏/并发/超时恢复 三项的初稿位）。
- **M-7b**：透镜库"典型历史缺陷"栏 —— 团队回填历史缺陷。
- **M-8**：覆盖审计 / Coverage Gate —— 回内网对照 Codetalks 原版机制，核对 §4.3 `checks.coverage` 与 §6.2 收尾门是否等价，差异补齐。
- **M-9**：auditor 提示词 —— 迁移 Codetalks 独立 Judge 原版提示词（M1 壳先以 §2.3.5 纲要 + §4.3 checks 清单顶替）。

**验证/确认类（需 codeagent 实际环境）**：
- **T-1**：确认 codeagent（opencode 改造版）的 agent 目录名（`.opencode/agent/` 单数 vs `agents/` 复数，§5.1）、skill 发现规则、以及 `adapters/opencode/.opencode` → 仓根 `.opencode` 的部署映射方式（软链/构建脚本）。先跑一个最小 agent 验证。
- **T-2**：确认 scenarios/ 落地路线（§5.2 路线 A 纯 md 引用 vs 路线 B 包成 opencode skill）。默认 A，请用户拍板。
- **T-3**：确认 codeagent 并发派 subagent 的上限与返回体量限制（§6.1），据此定 fan-out 批大小；超限降级串行。
- **T-4**：确认 MCP codehub 类工具是否存在及其名称（R-7.5 泛化探测在真实环境的表现）。
- **T-5**：确认深度模式任务 id 的时间戳来源（本执行环境不产生真实时间，§6.2）。
- **T-6**：实测 codeagent 中被 Task 调用的族 agent 能否与用户多轮对话——据此复核"输入询问前置到 Dispatcher / 回填询问写入报告待确认节"的设计（§2.1 第 5 条、§2.2.1 第 5 条）。

**待用户审定类（架构决策）**：
- **A-1**：挖掘剧本清单（§3，含规范调用名映射）是否认可、是否增删。
- **A-2**：四种交接工件 schema（§4.1–§4.4，含 progress/required_actions/manifest 结构化字段）是否认可。
- **A-3**：Dispatcher 模式判定判据与优先级（§2.1 第 4 条）、能力菜单表（含"输入要求/典型模式"列）、场景衔接规则表（§2.1）是否认可。
- **A-4**：3 族 agent 的 `mode` 取值（`all` vs `primary`，§5.1）。
- **A-5**：新增候选剧本 P10 初始化与卸载时序、P11 配置与规格参数盘点（§3）是否采纳。
