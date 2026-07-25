# PANGEA-TEST

面向存储黑盒测试的**测试导航系统**。Agent 扮演熟悉模块的资深开发，把代码内部逻辑翻译成外部可观测行为（协议报文、CLI 回显、告警、日志），产出流程讲解、SFMEA、测试场景、黑盒用例与定位分析。

> 服务领域：NVMe/TCP、iSCSI、NOF、KV、XNET、XRT 等协议及阵列底软（接口卡驱动、带内管理驱动、BMC、CLI、告警、日志）。

## Windows 快速开始

```powershell
git clone https://github.com/CyberShp/pangea-test.git
cd pangea-test
opencode
```

不需要软链、目录联接或复制配置。OpenCode 项目配置已直接放在仓库根目录 `.opencode/`。

启动后可用 Tab 切换：

- `dispatcher`：统一分诊与机器化工作流入口；
- `dev-expert`：代码逻辑、流程、SFMEA、黑盒场景与用例；
- `troubleshooter`：日志、抓包、失败用例与根因定位；
- `test-designer`：测试策略、可测试性、用例评审与缺陷单。

内部能力 Agent（如 `code-excavator`、`auditor`）为 `subagent + hidden`，不会占用 Tab 或 `@` 菜单，由授权的族 Agent 调用。

首次使用建议执行：

```text
@ping
```

通过判据：输出 `PING OK`，说明根目录 `.opencode`、中文路径、`core/` 与只读检索工具均可用。

## 两种使用方式

### 直接专家模式

通过 Tab 切到某个族 Agent，适合快速讲解、单点分析、日志定位、测试策略与用例评审。

### Dispatcher 机器化模式

```text
/analyze-module 分析对象=<模块或函数> 源码路径=<路径>
```

该模式会创建 task envelope、manifest、证据包和审计记录，支持：

- 结构化证据校验；
- 并行 Subagent；
- 断点恢复；
- Auditor 收尾门。

恢复未完成任务：

```text
/resume-run runs/<任务id>
```

## Python 与 pip 说明

- 单纯启动 OpenCode、Tab 切 Agent、使用普通分析能力：**不需要 pip**。
- `/analyze-module` 与 `/resume-run` 需要系统中可执行 `python`，但默认只使用 Python 标准库，**不需要安装第三方包**。
- 需要完整 JSON Schema Draft 2020-12 严格校验时，可选安装：

```powershell
python -m pip install -r runtime/requirements-strict.txt
```

安装后 `runctl.py` 会自动使用 `jsonschema`；未安装时使用内置基础校验器。也可通过环境变量明确选择：

```powershell
$env:PANGEA_VALIDATOR = "stdlib"     # 强制零依赖基础校验
$env:PANGEA_VALIDATOR = "jsonschema"  # 强制严格校验，未安装会报错
```

## 目录结构

```text
.opencode/   OpenCode 项目级 Agent、Command 与 Skill（直接可发现）
core/        平台无关纯 Markdown 资产
  shared/    全局铁律、纲领、调度规则、文档契约
  scenarios/ 场景作业流程
  playbooks/ 代码挖掘剧本
  lenses/    DFX 风险透镜
  methods/   测试设计方法论
  templates/ 输出模板
  protocols/ 协议知识
  modules/   模块知识
registry/    机器可读场景注册表
schemas/     JSON Schema 契约
runtime/     确定性 Run Store 与校验器
runs/        深度模式运行工件（不入库）
adapters/    其他载体适配预留；OpenCode 已提升为根目录原生入口
docs/        需求、架构与内网待办
```

## 架构分层

```text
Dispatcher（路由、输入引导、机器任务创建与恢复）
  → 场景族 Agent：dev-expert / troubleshooter / test-designer
    → 隐藏能力 Subagent：code-excavator / mr-reader / auditor / log-miner / pcap-analyzer
      → 平台无关知识资产：core/
```

铁律：**Agent 薄、Skill 厚、状态交给确定性 Runtime**。

## 当前机器化范围

首个机器化场景：`module-full-analysis`（模块全量分析）。

其余场景已经具备平台无关 Markdown 骨架，但在接入 Registry、Schema 与 Run Store 前，会明确标注为“文档工作流，未机器化”。

## 文档

- [需求说明书](docs/requirements.md)
- [架构设计书](docs/architecture.md)
- [内网待办清单](docs/内网待办清单.md)

内网迁移建议使用 `git clone` 或 `git bundle`，避免 Windows 解压 ZIP 时中文文件名编码异常。
