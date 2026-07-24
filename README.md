# PANGEA-TEST

面向存储黑盒测试的**测试导航系统**。Agent 扮演熟悉模块的资深开发，把代码内部逻辑翻译成外部可观测行为（协议报文/CLI 回显/告警/日志），产出流程讲解、SFMEA、测试场景、黑盒用例、定位分析。

> 服务领域：NVMe/TCP、iSCSI、NOF、KV、XNET、XRT 等协议及阵列底软（接口卡驱动、带内管理驱动、BMC、CLI、告警、日志）的黑盒测试。

## 文档
- [需求说明书](docs/requirements.md) —— 冻结决策 + 决策理由
- [架构设计书](docs/architecture.md) —— 蓝图细化，含内网待办清单

## 目录结构
```
core/        平台无关纯 md 资产（跨载体零改动复用）
  shared/    全局铁律·纲领·schema·调度规则
  scenarios/ 场景作业流程（加场景=加md）
  playbooks/ 挖掘剧本库（加挖掘能力=加md）
  lenses/    DFX 风险透镜库（找什么风险）
  methods/   测试设计方法论库（怎么推用例）
  templates/ 输出模板（格式受铁律 R-7.3 保护）
  protocols/ modules/  领域知识（随分析回填）
adapters/    各载体薄壳
  opencode/  .opencode/agents/*.md（v1 主力，codeagent 兼容）
  claude-code/  预留空壳（后补）
runs/        深度模式交接工件/断点（非资产，可 gitignore）
docs/        需求·架构·内网待办
```

## 架构分层
```
Dispatcher（调度：路由/引导/菜单/衔接/模式判定）
  → 场景族 agent（人设层）：dev-expert · troubleshooter(M2) · test-designer(M3)
    → 能力 subagent（脏活层）：code-excavator · mr-reader · auditor · log-miner(M2) · pcap-analyzer(M3)
      → 知识资产（core/ 纯 md）
```
铁律：**agent 薄、skill 厚**——agent 只是壳，流程/模板/知识全在 core/ 纯 md，跨平台移植只重写 adapters/。

## M1 交付范围（本里程碑）
- **Agent**（`adapters/opencode/.opencode/agents/`）：dispatcher · dev-expert · code-excavator · mr-reader（接口壳）· auditor（壳）
- **剧本**（`core/playbooks/`）：主干追踪 · 分支枚举 · 状态机提取 · 资源生命周期 · 异常传播 · 风险扫描（"5+1"）
- **种子两库**：透镜 资源泄漏/并发/超时恢复；方法论 状态转换/边界值分析（+ `_selector`）
- **场景**（`core/scenarios/`）：模块全量分析 · MR问题单分析（骨架 + 迁移占位）
- **模板**（`core/templates/`）：黑盒用例 · SFMEA · 两份报告模板
- **shared/**：溯源铁律 · 铁律总纲 · 八问纲领(骨架) · 观测手段目录(骨架) · 调度规则 · 证据包schema

## 内网待办
迁移类（Codetalks/内部资产）与验证类（codeagent 实测）见 [架构设计书末尾《内网待办清单》](docs/architecture.md#附-内网待办清单)（M-1~M-9、T-1~T-6、A-1~A-5）。

## 部署
用户先个人验证 → codehub 建个人仓 → 未来团队仓。首次部署先按 T-1 用最小 agent 验证 codeagent 的目录约定与 `core/` 引用可解析性，再全量铺开。
