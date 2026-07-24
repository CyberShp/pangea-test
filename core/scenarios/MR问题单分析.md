# 场景作业流程：MR / 问题单分析

> 归属族：dev-expert ｜ 对应 Codetalks 九步链路**裁剪版** ｜ 模式：**速度型 / 深度型**
> 上游：[architecture.md](../../docs/architecture.md) §8（深度型·MR 分析变体）、§2.3.2（mr-reader）｜ [requirements.md](../../docs/requirements.md) R-8.1、R-10.1
> **纪律**：本文只写"编排骨架"。九步裁剪的具体分析话术是 Codetalks 内部资产，一律留 `<!-- 待迁移 M-3 -->` 占位，**不得凭简介重写**。

---

## 1. 场景定位

针对一个 MR / 问题单，分析改动影响域与回归风险，产出针对性黑盒用例。是"改了什么 → 可能坏什么 → 怎么外部验证"的回归型分析，比模块全量分析聚焦、链路裁剪。

## 2. 输入（前置到 Dispatcher 收集）

- **MR 链接**（R-8.1：只给链接，问题背景由 mr-reader 从 MR 描述读取）**或** 用户粘贴的 diff + MR 描述。
- **模式**：速度型（"讲一下这个 MR / 快速看看"）或深度型（"评审 / 出回归用例集"），判据见 `shared/调度规则.md` 二；不明确则问用户。
- **任务 id**（仅深度型）：`MR问题单分析-<对象slug>-<日期>`。

## 3. 流程

> 九步在 MR 场景下裁剪为"以改动点为锚的影响域回归分析"。下列为编排步骤；裁剪后的分析内功心法留占位。

<!-- 待迁移 M-3：九步裁剪版的具体分析话术/研判细则，回内网用 Codetalks "问题单/MR 回归分析" 原文逐字继承替换，不得凭简介重写 -->

1. **MR 获取（一律经 mr-reader）**：Task 派 `mr-reader` → 产出 `mr_summary`（schema §4.2b：title/description/changed_files/change_intent/**risk_hotspots**/raw_excerpts）。
   - MCP 泛化探测 codehub 类工具拉 MR（R-7.5，不硬编码工具名）；无则请用户粘贴 diff。
   - **速度型例外**（architecture §2.3.2）：MR 获取仍走 mr-reader，但 `mr_summary` **内联消费、不落 `runs/`**。
2. **选剧本**：族 agent 按 `mr.risk_hotspots` 决定挖哪些剧本。常用：
   - `调用链影响域`（改动点的上游 caller / 下游 callee / 受影响外部特性）；
   - `异常传播`（改动是否影响错误产生/传播/处置）；
   - `风险扫描` × 透镜（改动命中的 DFX 风险，种子透镜：资源泄漏/并发/超时恢复）。
   - 其余剧本按 hotspots 特征追加（如涉及状态迁移则加 `状态机提取`）。
3. **fan-out excavator**：深度型并行派发选中的 code-excavator 实例（只读，各注入剧本/透镜），证据包落 `runs/<任务id>/`，manifest 登记（含 `artifact_type=mr_summary` 那条）。速度型走内联裁剪、不落盘。
4. **汇总**：族 agent 合并证据包 → 回归风险点清单 + **针对性用例**（用 `methods/_selector.md` 选法推导，聚焦受影响面而非全量覆盖）。

<!-- 待迁移 M-3 结束 -->

## 4. 双模式差异

| 环节 | 速度型 | 深度型 |
|---|---|---|
| MR 获取 | mr-reader（内联消费）| mr-reader（落 `runs/`）|
| 挖掘 | 内联轻量读码，不走 code-excavator | fan-out code-excavator + 落工件 |
| 断点恢复 | 无（不落 runs/）| 读 manifest 续跑（architecture §6.2）|
| auditor | 不过 | 过（收尾门）|
| 产出 | 内联回归分析（Markdown）| 完整报告 |

> 依据 R-7.6 / architecture §2.3.2：速度型"不走能力 subagent"限缩解释为——不走 code-excavator、不落 `runs/`；MR 获取因需泛化探测/压缩证据包，**始终经 mr-reader**，避免探测逻辑在族 agent 内重实现而漂移。

## 5. 收尾门（深度型）

1. **auditor 复核**：Task 派 `auditor` → `audit_opinion`（溯源/黑盒可执行性/覆盖/格式四组 checks）。
2. **覆盖审计 PASS** 才完成；FAIL/CONCERNS → 按 `required_actions` 回挖（≤ 2 轮，超轮带 CONCERNS 出报告，交用户裁决）。
3. **产出**：报告落 `templates/报告-MR问题单分析.md`（MR 摘要 → 改动影响域 → 回归风险点 → 针对性用例 → 审计结论 → **待用户确认**）。
4. **回填询问**：R-7.4 询问写入报告"待用户确认"节，不在对话内提问（T-6）。

## 6. 场景衔接（供 Dispatcher 推荐）

完成后推荐下一步（architecture §2.1 衔接表）：共性问题排查（同类隐患）→ FST 逃逸复盘（若属逃逸）。
