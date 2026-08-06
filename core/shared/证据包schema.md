# 交接工件 schema（代码证据、风险卡与审计）

> 内部 DFX 子 Agent 与主 Agent 之间的机器可消费契约。完整型的断点恢复、审计回挖闭环全部依赖本文字段。
> 对应架构 §4（§4.1–§4.5）。schema_version 为演进预留；字段以真实跑通为终检。

---

## 4.1 代码证据包 `code_evidence`（code-excavator 产出，分剧本变体）

**公共外层（所有剧本共用）**以 `schemas/code-evidence.schema.json` 的 1.0 契约为准。`artifact_id` 是必填稳定标识，`status` 可为 `complete`、`partial` 或 `failed`：

```json
{
  "artifact_type": "code_evidence",
  "schema_version": "1.0",
  "artifact_id": "CE-MAIN-001",
  "playbook": "主干追踪",
  "target": "connection-recovery",
  "lens": null,
  "source_ref": {
    "repo_or_path": "driver",
    "commit_or_mr": "0123456789abcdef0123456789abcdef01234567"
  },
  "status": "complete",
  "progress": {
    "done_steps": [1, 2],
    "pending_steps": [],
    "resume_hint": null
  },
  "coverage_note": "连接建立和压力回落路径已完成源码核对",
  "findings": [{
    "location": "driver/resource.c:98-142",
    "observation": "超规格拒绝分支依赖后续连接额度回收"
  }],
  "inferences": [{
    "claim": "额度回收延迟可能扩大业务恢复时间",
    "basis": "拒绝分支和回收分支由不同事件推进",
    "verify_method": "施加并解除连接压力后观察新连接恢复时间"
  }],
  "open_questions": []
}
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
  risk_hotspots: []            # {位置, 疑点} 供主 Agent 路由 DFX 能力包
raw_excerpts: []               # diff/描述原文（禁止改写）
```

## 4.3 统一风险卡 `risk_card`

风险卡字段以 `core/capabilities/risk-card-contract.md` 为准。一个风险可由多个 DFX 包共同署名；主 Agent 合并后保留全部原始证据与跨维度关联。

## 4.4 auditor 审查意见 `audit_opinion`

`audit_opinion` 固定使用 2.0，并绑定 `internal/report-model.json` 及其 SHA-256。每个 violation/gap 必含 `anchor`、`issue`、`impact`、`verification`；每个 required action 必含 `action_type`、`reason`、`anchor`、`verification`：

```json
{
  "artifact_type": "audit_opinion",
  "schema_version": "2.0",
  "audited_artifact": "internal/report-model.json",
  "audited_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "verdict": "CONCERNS",
  "checks": {
    "traceability": {"verdict": "PASS", "violations": [], "gaps": []},
    "blackbox_executability": {"verdict": "PASS", "violations": [], "gaps": []},
    "coverage": {
      "verdict": "CONCERNS",
      "violations": [],
      "gaps": [{
        "anchor": "risks[0].coverage_gap",
        "issue": "关联固件状态缺少外部观测证据",
        "impact": "该风险的恢复结论目前无法闭环",
        "verification": "补充诊断日志后复核恢复状态与业务结果"
      }]
    },
    "format_compliance": {"verdict": "PASS", "violations": [], "gaps": []}
  },
  "required_actions": [{
    "action_type": "add_evidence",
    "reason": "补充固件恢复状态对应的诊断日志证据",
    "anchor": "risks[0].evidence",
    "verification": "复核新增日志能够支持风险恢复结论"
  }]
}
```

**聚合规则**：任一子项 `FAIL` 则总 verdict 为 `FAIL`；无 `FAIL` 但任一子项为 `CONCERNS` 则总 verdict 为 `CONCERNS`；四项均为 `PASS` 才能总 `PASS`。

## 4.5 `run_manifest`（runs/<任务id>/manifest.md，断点恢复索引）

```yaml
artifact_type: run_manifest
schema_version: 0.1
task_id: <生成规则见 core/shared/调度规则.md>
scenario: <场景 skill 文件名>
target: <分析对象>
mode: deep                     # 速度型不落 runs/
inputs_ref: []                 # MR 链接 / 源码路径 / mr_summary 工件名
planned_artifacts:
  - artifact_type: code_evidence | risk_card | mr_summary | log_summary | pcap_summary
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

## 4.6 模板最小字段
见 `core/templates/` 各文件。核心：黑盒用例必含 `用例编号`（= audit `case_id` 锚点）、`外部触发手段`、`观测手段`、`观测判据(PASS/FAIL)`、`推导方法论`、`覆盖目标`（可选，coverage 审计锚点）。
