# 交接工件 schema（四种 + M1 模板字段）

> 能力 subagent 与族 agent 之间的机器可消费契约。深度模式的断点恢复、auditor 回挖闭环全部依赖本文字段。
> 对应架构 §4（§4.1–§4.5）。schema_version 为演进预留；字段以真实跑通为终检。

---

## 4.1 代码证据包 `code_evidence`（code-excavator 产出，分剧本变体）

**公共外层（所有剧本共用）**：

```yaml
artifact_type: code_evidence
schema_version: 0.1
playbook: <规范剧本名>          # 见 playbooks/ 文件名，如 主干追踪 / 风险扫描
target: <挖掘对象>
lens: <透镜名 | null>          # 仅 风险扫描 填
source_ref:
  repo_or_path: <源码路径>
  commit_or_mr: <可选>
status: complete | partial     # partial 支持断点恢复
progress:                      # 断点续挖的结构化依据（与剧本"步骤"编号对应）
  done_steps: []               # 已完成步骤号，如 [1, 2]
  pending_steps: []            # 未完成步骤号
  resume_hint:
    file: <续挖文件>
    symbol: <续挖符号>
    note: <从哪继续>
coverage_note: <覆盖/遗漏说明（人读；机器续挖以 progress 为准）>
findings: <剧本专属字段，见每个 playbooks/*.md 的"证据包字段">
inferences:                    # 溯源双轨制：推断单列
  - claim: <推测内容>
    basis: <依据>
    verify_method: <黑盒验证方法>
open_questions: []
```

> **溯源纪律落到字段**：`findings` 内每条必须带 `位置: 文件:行号`；给不出行号的移到 `inferences[]`。
> **剧本变体** = `findings` 换成对应 playbook 的"证据包字段"。

## 4.2 日志/报文摘要 与 MR 摘要

**4.2a `log_summary` / `pcap_summary`（log-miner / pcap-analyzer）**：
```yaml
artifact_type: log_summary | pcap_summary
schema_version: 0.1
source_ref: { path_or_link: <路径>, range: <行号/时间/报文序号区间> }
timeline:
  - { ts: <时间戳>, event: <事件>, raw_ref: <log:12345 | pkt:88>, note: <解读> }
key_signals: []                # 错误码/告警/异常报文/重传等
correlations: []               # 事件间关联【推测标注】
raw_excerpts: []               # 原文片段（禁止改写）
```

**4.2b `mr_summary`（mr-reader）**：
```yaml
artifact_type: mr_summary
schema_version: 0.1
source_ref: { path_or_link: <MR 链接 | "用户粘贴"> }
mr:
  title: <标题>
  description: <描述原文摘要>
  changed_files: []            # {file, hunks 概述}
  change_intent: <改动意图>
  risk_hotspots: []            # {位置, 疑点} 供 dev-expert 选剧本
raw_excerpts: []               # diff/描述原文（禁止改写）
```

## 4.3 auditor 审查意见 `audit_opinion`

```yaml
artifact_type: audit_opinion
schema_version: 0.1
audited_artifact: <被审产出标识/路径>
verdict: PASS | CONCERNS | FAIL
checks:
  traceability:                # R-7.1
    verdict: PASS | FAIL
    violations: []             # {location, issue}
  blackbox_executability:      # R-7.2
    verdict: PASS | FAIL
    violations: []             # {case_id, issue}
  coverage:                    # 覆盖审计 / Coverage Gate
    verdict: PASS | CONCERNS | FAIL
    dimension: <全透镜浅扫 | 分支覆盖 | 状态覆盖 …>
    gaps: []                   # {未覆盖项, 原因}
  format_compliance:           # R-7.3
    verdict: PASS | FAIL
    violations: []
required_actions:              # 结构化，可机器映射回重挖调用
  - action_type: re_excavate | fix_format | add_evidence | rewrite_case
    playbook: <规范剧本名 | null>
    target: <对象 | null>
    lens: <透镜 | null>
    reason: <原因>
    ref_violation: <指向上方某条 violation>
```

**聚合规则**：任一子项 FAIL ⇒ 总 FAIL；无 FAIL 但 coverage=CONCERNS ⇒ 总 CONCERNS；全 PASS ⇒ PASS。
`case_id` 引用黑盒用例模板的"用例编号"字段。

## 4.4 `run_manifest`（runs/<任务id>/manifest.md，断点恢复索引）

```yaml
artifact_type: run_manifest
schema_version: 0.1
task_id: <见本目录 调度规则.md>
scenario: <场景 skill 文件名>
target: <分析对象>
mode: deep                     # 速度型不落 runs/
inputs_ref: []                 # MR 链接 / 源码路径 / mr_summary 工件名
planned_artifacts:
  - artifact_type: code_evidence | mr_summary | log_summary | pcap_summary
    playbook: <规范剧本名 | null>   # code_evidence 必填
    target: <对象>
    lens: <透镜 | null>
    status: pending | partial | complete
    artifact_file: <runs/<id>/ 下文件名 | null>
summary_status: pending | partial | complete
audit:
  rounds: 0                    # 已回挖轮数（上限 2）
  status: pending | PASS | CONCERNS | FAIL
  opinion_file: <audit_opinion 工件名 | null>
```

> **红线**：字段永远限于本次深度任务的工件状态。禁止加"用例执行进度/项目里程碑"（越界为被否决的项目状态文件，见 铁律总纲 R-12）。

## 4.5 M1 模板最小字段
见 [../templates/](../templates/) 各文件。核心：黑盒用例必含 `用例编号`（= audit `case_id` 锚点）、`外部触发手段`、`观测手段`、`观测判据(PASS/FAIL)`、`推导方法论`。
