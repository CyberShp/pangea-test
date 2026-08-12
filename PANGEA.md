# PANGEA v1 总则

本文件是 PANGEA 唯一的规范性规则来源。Agent、Command、Skill、README、Runtime、
Schema、测试与报告只能引用或实现本文件，不能增加第二套完成条件、隐藏契约或替代流程。

## 1. 产品目标

PANGEA 对已登记仓库中的冻结源码范围执行只读语义分析，输出可追溯的中文代码分析、
模块级六维 DFX、SFMEA、风险、黑盒/灰盒测试设计和证据边界，并生成同内容的 Markdown
与离线 HTML 报告。首版正式支持 C/C++ 模块分析。

## 2. 唯一状态机

```text
draft -> confirmed -> prepared -> planned -> analyzing -> composed
      -> reviewing -> completed
```

任何执行阶段都可进入 `failed`。只有用户显式 retry 才回到失败阶段。语义 Review 为
`fail` 时 Run 保持 `reviewing`；显式 retry 回到 `analyzing`，保留历史 execution receipt，
重新生成一个新的 analysis revision。`run.json` 是状态、revision、进度、错误、Review 和
报告位置的唯一真相源。

## 3. 固定流水线与角色边界

```text
冻结源码 -> semantic slices -> slice fragments -> module semantics
         -> one module DFX -> risk/SFMEA/test design -> review -> report
```

- `pangea-test`：确认目标和范围，调用 Runtime，展示真实状态；不读源码代写语义。
- `analysis-worker`：一次只读取一个冻结 context。只产出 behavior、flow、branch、state、
  resource、concurrency、error chain、risk candidate 和 gap；不产出 DFX、正式风险、SFMEA、
  scenario、test case 或报告。
- `composer`：只合并冻结 fragments，删除被全局证据推翻的对象并修正跨 slice 关系；不做
  DFX 和测试设计。
- `dfx-analyst`：在模块语义冻结后执行一次，输出六个模块级 assessment；不重复 slice 分析。
- `risk-adjudicator`：小批量独立判定 risk candidates 是否成立；测试不可执行不能反向驳回风险。
- `risk-analyst`：只合并 confirmed candidates 为 canonical risk 并生成 SFMEA，不重新裁决。
- `test-designer`：只从已确认 risk 与 behavior 派生 scenario 和 test case，不参与风险成立判定。
- `reviewer`：独立读取最终 analysis 与证据，返回 Review；不修改、补写或代替任何分析。

所有模型角色禁止调用工具、读取仓库、写文件或获取 context 外信息。Runtime 负责保存输入、
原始模型事件、token、finish reason、最终文本与验证结果。

## 4. 唯一成功定义

模型调用只有同时满足以下条件才成功：

1. 进程正常退出；
2. 原始 finish reason 为且仅为 `stop`；
3. 最终文本非空；
4. 最终文本是且仅是一个 JSON 对象；
5. JSON 符合 context 内同一份公开 contract；
6. evidence 落在提供的冻结源码范围；
7. execution receipt 和正式结果均已写入。

`finish=length`、空输出、截断、多 JSON、无效 JSON、Schema 错误、越界 evidence 和工具调用
均为真实失败。Runtime 不自动重试、不拼接残片、不让主 Agent 修补模型结果。

## 5. 语义模型

Slice 语义对象包括：

- Behavior：入口、操作或外部结果；
- Flow：触发、顺序步骤和完成结果；
- Branch：判断条件及不同结果；
- State：事件驱动的状态迁移；
- Resource：申请、所有权、使用、释放与异常回滚；
- Concurrency：执行上下文、共享状态、同步和销毁时序；
- Error Chain：触发、传播、处理、恢复和外部影响；
- Risk Candidate：待全局证据判定的风险候选；
- Gap：当前范围内缺少的证据及最小补充要求。

所有事实关系必须显式声明并由最小充分 evidence 支持。文件相邻、同一 slice 或同一标题不能
自动产生关系。Composer 可以删除任何被全局证据推翻的语义对象，但每个删除都必须留下理由。

源码覆盖与语义覆盖必须分开：完整读取源码行只能证明 source coverage；只有入口、流程、分支、
状态、资源、并发、错误传播及 Gap 均有可复核处置时，才能描述 semantic coverage。报告不得用
文件数或行数直接宣称语义完整。

## 6. Skills 与方法路由

Skills 是非规范性的分析方法，不定义生命周期、完成门禁、重试、Review 或报告格式。Runtime
根据冻结源码中的语言、仓库、目标、符号和领域信号选择最少适用方法，并在 context 与报告中
记录名称和命中原因；不使用 SHA 或内容哈希作为门禁。

共同底座覆盖 C/C++ 调用、分支、状态、所有权、清理、并发和错误路径。SPDK、iSCSI、资源恢复
及 NVIDIA DFX 等领域方法只在证据匹配时加载。厂商知识不能仅凭任务名称推断实现事实。

## 7. 模块级 DFX

模块语义冻结后，每个 analysis revision 只允许一个成功的 DFX 调用。六个维度固定为：

1. 功能与状态；
2. 资源与规格；
3. 性能与压力；
4. 并发与异常；
5. 升级与兼容；
6. 可靠性与一致性。

每个维度输出 `summary + findings[]`。`findings[]` 允许零到多条，类型只允许：
`strength`、`risk`、`limitation`、`gap`、`recommendation`。零 finding 必须在 summary 中说明
当前证据为何没有形成具体 finding。禁止为满足数量而生成一维一条或模板化结论。

DFX risk finding 只是 risk candidate。相同因果链跨多个维度时合并为一个 canonical risk，
并保留全部维度关系。

## 8. 风险、SFMEA 与测试设计

Risk candidate 必须逐项得到 `confirmed` 或 `dismissed` 决定。Canonical risk 包含复现条件、
传播、外部影响、观测、恢复、严重度、可信度、证据和关联语义。

风险是否成立与测试是否可执行是两个独立判断。`testability` 只允许：

- `blackbox_ready`：外部入口、触发和判据完整；
- `graybox_ready`：需要日志、指标、诊断或控制时序，但最终仍有外部结果；
- `blocked`：风险成立但缺少环境、接口、观测或关联证据，必须保留 `missing_evidence`。

`blocked` 风险必须保留在风险账本、SFMEA 和报告中，可以没有 test case。删除、否决或无法执行
某个测试，不能删除已成立风险。`blackbox_ready` 和 `graybox_ready` 风险必须派生至少一个
可定位失败原因的 scenario 与 test case。

测试只从 confirmed risk 和 behavior 派生。用例必须包含参数、前置、操作、预期、观测与清理；
一例可覆盖多个真正共享触发和判据的风险，不得形成全量风险笛卡尔绑定。

## 9. Review、报告与完成

Review 由 Runtime 确定性结构检查和独立 reviewer 语义检查组成，合并为唯一 `review.json`。
至少检查：证据正确性、因果链、语义覆盖边界、风险判定、DFX 非模板化、blocked 风险保留、
测试可执行性和关系是否被错误扩大。

报告至少包含：范围与方法、源码地图、Behavior、Flow、Branch、State、Resource、Concurrency、
Error Chain、六维 DFX findings、风险账本、SFMEA、场景、用例、风险—用例映射、Gap、合并决策、
Review 和覆盖边界。HTML 与 Markdown 内容一致；HTML 支持搜索、按 finding/risk/testability 筛选
及对象间跳转。

只有当前 analysis revision 的 Review 为 `pass`，且 Markdown 与 HTML 已从同一 analysis 生成，
Run 才能进入 `completed`。聊天中的 completed、外层 Task 结束或文件存在不能单独表示交付完成。

## 10. 正式工件

```text
pangea-data/runs/<run-id>/
  run.json
  source/  source-map.json  plan.json
  contexts/  executions/  results/
  module-context.json  module-result.json
  dfx-context.json  dfx-result.json
  risk-context.json  risk-result.json
  test-design-context.json  test-design-result.json
  analysis.json  review-context.json  review.json  events.jsonl

pangea-data/reports/<run-id>/
  report.md  report.html
```

不使用 SHA、内容哈希、签名 receipt、bundle seal 或 hash-change 作为完整性或完成门禁。正式 JSON
使用同目录临时文件加原子替换写入；execution receipt 按 attempt 追加，retry 不覆盖历史证据。
