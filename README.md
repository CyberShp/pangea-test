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

## 交付范围（M1+M2+M3 平台无关资产全量骨架）

> 所有平台无关 md 资产已补齐。**内网迁移项（M-x）仍留占位、环境验证项（T-x）待内网实测**——见 [内网待办清单](docs/内网待办清单.md)。

- **Agent ×10**（`adapters/opencode/.opencode/agents/`）：
  - 调度：dispatcher
  - 族（人设层）：dev-expert · troubleshooter · test-designer
  - 能力（脏活层）：code-excavator · mr-reader(接口壳) · auditor · log-miner · pcap-analyzer
  - 部署自检：ping
- **挖掘剧本 ×12**（`core/playbooks/`）：主干追踪 · 分支枚举 · 状态机提取 · 资源生命周期 · 异常传播 · 风险扫描 · 调用链影响域 · 并发上下文识别 · 超时重试盘点 · 协议报文收发路径 · 初始化卸载时序 · 配置规格盘点
- **DFX 风险透镜 ×18**（`core/lenses/`，6 维度）：可靠性(资源泄漏/并发/超时恢复/数据完整性/异常处理覆盖/恢复路径) · 可用性(单点故障/降级可用/过载保护) · 性能(性能退化/时延长尾/资源争用) · 规格(对外规格符合性/内部实现规格) · 韧性(异常输入健壮性/故障注入韧性) · 升级(版本兼容性/升级中断回滚) + 可服务性引用桩 + `_index`
- **测试设计方法论 ×12**（`core/methods/`）：状态转换 · 边界值分析 · 等价类划分 · 数据组合测试 · 判定表覆盖 · 判定点覆盖 · 处理周期测试 · 数据生命周期 · 错误推测 · 端到端场景 · MAE流程覆盖 · 时间维度覆盖 + `_selector`
- **场景 ×13**（`core/scenarios/`）：
  - dev-expert：模块全量分析 · MR问题单分析 · FST逃逸复盘 · 共性问题排查 · 专项风险分析 · 原理讲解
  - troubleshooter：日志定位 · 失败用例三分类 · 抓包辅助定位
  - test-designer：可测试性分析 · 测试策略 · 用例评审 · 缺陷单撰写
- **模板 ×5**（`core/templates/`）：黑盒用例 · SFMEA · 缺陷单 · 报告-模块全量分析 · 报告-MR问题单分析
- **shared/ ×6**：溯源铁律 · 铁律总纲 · 八问纲领(骨架,M-1) · 观测手段目录(骨架,M3合流) · 调度规则 · 证据包schema

> 仍留内网占位的迁移内容：八问纲领全文(M-1) · 九步链路分析话术(M-2/M-3) · 专项风险研判(M-7a) · 日志定位研判(M-4) · mr-reader 内部实现(M-6) · auditor Judge 提示词(M-9) · 黑盒用例团队版(M-5)。

## 内网待办
迁移类（Codetalks/内部资产）与验证类（codeagent 实测）见 [架构设计书末尾《内网待办清单》](docs/architecture.md#附-内网待办清单)（M-1~M-9、T-1~T-6、A-1~A-5）。

## 部署

路径：个人验证 → codehub 建个人仓 → 未来团队仓。**迁移入内网走 `git clone` / `git bundle`，勿走 zip**（zip+Windows 解压会把中文文件名按 GBK 猜码搞乱）。

### T-1 最小验证（首次部署必做，通过前勿全量铺开）

```bash
# 1. 在仓根建部署软链（已被 .gitignore 忽略，不会误提交）
cd <仓根>
ln -s adapters/opencode/.opencode .opencode

# 2. 启动 codeagent/opencode，调用最小验证 agent
#    @ping   （或经 Task 调用）
```

`ping` agent（`adapters/opencode/.opencode/agents/ping.md`）一次验证四件事：agent 目录约定（单/复数）、中文路径可读、`core/` 仓根相对引用可解析、Read/Glob/Grep 在 `edit/bash deny` 下可用。

- **通过判据**：输出 `PING OK：…`。
- **失败排查**：① 目录改试 `.opencode/agent/`（单数）；② 确认 cwd 在仓根；③ 若 Grep 失败 = codeagent 的 grep 走 shell 被 `bash: deny` 连坐 → 按其权限语义放行只读检索；④ 中文路径失败 → 检查 locale 为 UTF-8。
- 全部通过后再部署其余 agent，并继续走 [内网待办清单](docs/内网待办清单.md) 的 T-2~T-6。
