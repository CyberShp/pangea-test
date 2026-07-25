# PANGEA-TEST

面向存储黑盒测试的**项目级测试导航工作台**。Agent 把代码内部逻辑翻译成协议报文、CLI 回显、告警和日志等外部可观测行为，产出流程讲解、SFMEA、测试场景、黑盒用例与定位分析。

> 服务领域：NVMe/TCP、iSCSI、NOF、KV、XNET、XRT 等协议及阵列底软。

## Windows 快速开始

```powershell
git clone https://github.com/CyberShp/pangea-test.git
cd pangea-test
opencode
```

不需要软链、目录联接或复制配置。启动后可用 Tab 切换：

- `pangea-test`：主入口，负责统一分诊、项目识别和托管任务编排；
- `dev-expert`：代码逻辑、流程、SFMEA、黑盒场景与用例；
- `troubleshooter`：日志、抓包、失败用例与根因定位；
- `test-designer`：测试策略、可测试性、用例评审与缺陷单。

首次使用先执行：

```text
/doctor
```

## 用户不需要记命令

用户可以直接说：

```text
结合最新设计、需求和覆盖率，对 NVMe/TCP 连接恢复做全量分析，输出 SFMEA 和测试用例。
```

PANGEA-TEST 主 Agent 会自动：识别当前项目 → 扫描输入 → 检索团队资产 → 判定托管模式 → 创建隔离工作区。

`/analyze-module`、`/project-*`、`/asset-search` 只保留给调试、自动化和精确控制。

## 两种使用方式

### 直接专家模式

通过 Tab 或 `@` 进入族 Agent，适合快速讲解、单点读码、日志片段定位和少量用例评审：

- 不创建托管工作区；
- 不需要 Python；
- 不承诺断点恢复、全覆盖或独立审计。

### 托管任务模式

适合“全量、系统性、SFMEA、正式用例集、覆盖审计、结合设计/需求/覆盖率”。它具备：

- Evidence 与 manifest 一致性检查；
- 并行 Subagent；
- 断点恢复；
- Auditor 收尾门；
- `required_actions` → 受控 `rework_plan`；
- 最大审计轮数熔断；
- 输入和长期资产锁定；
- 中间件和正式输出分离。

## 预设空间

```text
source/      被分析源码，只读、零写入
inputs/      用户设计、需求、覆盖率、已有用例、日志、pcap
workspace/   project/workflow/run 维度的中间件、证据、审计和草稿
outputs/     正式交付物，按 project/workflow/run 区分
projects/    项目配置和当前项目指针
assets/      团队长期测试资产
```

源码放入 `source/<project>/` 后，用户可说“把这个源码建成 PANGEA 项目”。系统会创建：

```text
projects/<project>/project.json
inputs/<project>/
workspace/<project>/
outputs/<project>/
```

**PANGEA 不在源码仓库内部创建任何配置、缓存、中间件或输出。**

## 工作流和运行隔离

```text
workspace/<project>/module-full-analysis/<run-id>/
workspace/<project>/mr-analysis/<run-id>/
workspace/<project>/log-troubleshooting/<run-id>/
workspace/<project>/test-strategy/<run-id>/
```

每次正式任务生成：

```text
manifest.json
inputs.lock.json
artifacts.json
run-context.json
evidence/
audit/
rework/
final/
```

正式交付对应发布到：

```text
outputs/<project>/<workflow>/<run-id>/
```

并用 `latest.json` 指向最近一次输出。

## 输入资料管理

`inputs/<project>/` 可按以下目录组织：

```text
design/
requirements/
coverage/
existing-cases/
logs/
pcaps/
mr/
defects/
reference/
```

`input scan` 会生成 Catalog，记录角色、格式、版本提示、hash、大小和修改时间。正式任务使用 `inputs.lock.json` 冻结本次实际消费的资料。

## 测试资产如何被 Agent 使用

`assets/` 保存跨项目复用的：

- 特性知识；
- 测试用例；
- 历史经验；
- 故障模式；
- 缺陷模式；
- 观测点。

分工：

```text
资产 = 知道什么
Skill = 什么时候、按什么条件检索和使用
CLI = 确定性索引、搜索和路径操作
Agent = 综合判断和编排
```

调用链：`自然语言 → Agent → Skill → CLI → 结构化结果`。Agent 必须通过 Catalog 检索，禁止每次遍历全部资产。

## 端到端 Smoke

首次接入新环境或修改 Agent/Runtime 后运行：

```text
/smoke-module
```

它会分析仓内 `tests/fixtures/mini-storage-module/`，验证：

```text
Doctor → 创建 run → pangea-test → dev-expert → code-excavator
→ Evidence 校验入库 → 汇总 → Auditor → 受控回挖
```

## Python 与 pip

- 启动 OpenCode、Tab 切 Agent、直接专家模式：不需要 Python 或 pip。
- Doctor、项目管理和托管任务需要可执行 `python`，默认只使用标准库。
- 可选严格 JSON Schema 校验：

```powershell
python -m pip install -r runtime/requirements-strict.txt
```

## 确定性工具（调试/自动化）

```powershell
python -m tooling.pangea_cli project init --project-id nvme-tcp
python -m tooling.pangea_cli project show
python -m tooling.pangea_cli input scan
python -m tooling.pangea_cli asset index
python -m tooling.pangea_cli asset search --profile nvme --type failure_mode
python -m tooling.pangea_cli workflow start --workflow-id module-full-analysis --target connection
python -m tooling.pangea_cli workflow publish --run-dir <run-dir>
```

## Runtime 分工

- `runtime/runctl.py`：task、manifest、Evidence 入库、审计入库与恢复；
- `runtime/managed.py`：Evidence/manifest 强一致性和受控回挖；
- `runtime/doctor.py`：环境和预设空间诊断；
- `tooling/pangea_cli/`：项目、输入、资产和工作流空间管理。

## 当前机器化范围

- `module-full-analysis`：已机器化；
- `mr-analysis`、`log-troubleshooting`、`test-strategy`：已登记空间和交付契约，但仍标记为未机器化，Runtime 会拒绝伪托管执行。

## 迭代文档

- [迭代 005：主 Agent 重命名](docs/iterations/005-primary-agent-rename.md)
- [迭代 004：Workspace & Asset Platform](docs/iterations/004-workspace-asset-platform.md)
- [需求说明书](docs/requirements.md)
- [架构设计书](docs/architecture.md)
- [内网待办清单](docs/内网待办清单.md)
