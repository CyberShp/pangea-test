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

- `dispatcher`：统一分诊与托管任务入口；
- `dev-expert`：代码逻辑、流程、SFMEA、黑盒场景与用例；
- `troubleshooter`：日志、抓包、失败用例与根因定位；
- `test-designer`：测试策略、可测试性、用例评审与缺陷单。

内部能力 Agent 为 `subagent + hidden`，不会占用 Tab，由授权族 Agent 调用。

首次使用先执行：

```text
/doctor
```

它会分别判断“直接专家模式”和“托管任务模式”是否可用，并把严格 JSON Schema 未安装标为可选 WARN，而不是系统不可用。

## 两种使用方式

### 直接专家模式

通过 Tab 或 `@` 进入某个族 Agent，适合快速讲解、单点读码、日志片段定位、少量用例评审：

- 不创建 `runs/`；
- 不需要 Python；
- 不承诺断点恢复、全覆盖或独立审计。

### 托管任务模式

适合“全量、系统性、SFMEA、正式用例集、覆盖审计”。它会创建 task envelope、manifest、证据包、审计与整改计划。

```text
/analyze-module 分析对象=<模块或函数> 源码路径=<路径>
```

恢复未完成任务：

```text
/resume-run runs/<任务id>
```

托管模式具备：

- Evidence 与 manifest 一致性检查；
- 并行 Subagent；
- 断点恢复；
- Auditor 收尾门；
- `required_actions` → 受控 `rework_plan`；
- 自动任务与人工动作分离；
- 最大审计轮数熔断。

## 端到端 Smoke

首次接入新环境或修改 Agent/Runtime 后，运行：

```text
/smoke-module
```

它会分析仓内 `tests/fixtures/mini-storage-module/`：一个可编译的小型存储连接模块，内含状态机、重试、日志观测点，以及故意植入的资源泄漏和 inflight 计数累积风险。

Smoke 主要验证：

```text
Doctor → 创建 run → Dispatcher → dev-expert → code-excavator
→ Evidence 校验入库 → 汇总 → Auditor → 受控回挖
```

fixture 的 README 只提供预期风险基准，Agent 不得用基准内容替代源码证据。

## Python 与 pip 说明

- 启动 OpenCode、Tab 切 Agent、直接专家模式：**不需要 Python 或 pip**。
- `/doctor`、`/analyze-module`、`/resume-run`、`/smoke-module` 需要可执行 `python`，默认只使用标准库，**不需要第三方包**。
- 可选严格校验：

```powershell
python -m pip install -r runtime/requirements-strict.txt
```

环境变量：

```powershell
$env:PANGEA_VALIDATOR = "stdlib"      # 强制零依赖基础校验
$env:PANGEA_VALIDATOR = "jsonschema"  # 强制 Draft 2020-12 严格校验
```

## Runtime 分工

- `runtime/runctl.py`：稳定基础层，负责 task、manifest、Evidence 入库、审计入库与恢复；
- `runtime/managed.py`：托管增强层，负责 Smoke 唯一 run、Evidence/manifest 强一致性、Auditor 回挖计划；
- `runtime/doctor.py`：零依赖环境诊断。

Auditor 的 `required_actions` 不会被 Agent 自由解释：

- 合法 `re_excavate` 且 target/playbook/lens 均在 Registry 内 → `next_tasks`；
- 格式修复、用例改写、越权或超过轮数 → `manual_actions`；
- 同一整改重复提出 → `skipped_duplicates`。

## 目录结构

```text
.opencode/   OpenCode 项目级 Agent、Command 与 Skill
core/        平台无关 Markdown 资产
registry/    机器可读场景注册表
schemas/     JSON Schema 契约
runtime/     确定性状态控制、托管增强和诊断
runs/        托管任务工件（不入库）
tests/       Runtime 测试、Golden 计划和 Smoke fixture
adapters/    其他载体适配预留
docs/        需求、架构与内网待办
```

## 架构分层

```text
Dispatcher（路由、模式分流、托管任务创建与恢复）
  → 场景族 Agent：dev-expert / troubleshooter / test-designer
    → 隐藏能力 Subagent：code-excavator / mr-reader / auditor / log-miner / pcap-analyzer
      → 平台无关知识资产：core/
```

铁律：**Agent 薄、Skill 厚、状态交给确定性 Runtime**。

## 当前机器化范围

首个机器化场景：`module-full-analysis`（模块全量分析）。

其余场景已有 Markdown 骨架，但接入 Registry、Schema 与 Run Store 前，会明确标注为“文档工作流，未机器化”。

## 文档

- [需求说明书](docs/requirements.md)
- [架构设计书](docs/architecture.md)
- [内网待办清单](docs/内网待办清单.md)

内网迁移建议使用 `git clone` 或 `git bundle`，避免 Windows 解压 ZIP 时中文文件名编码异常。
