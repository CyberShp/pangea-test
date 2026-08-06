# 迭代 006：Architecture v2 迁移

## 目标

从“主 Agent 分诊到三族专家”的测试导航工作台，迁移为一个用户可见的 `pangea-test` 测试 Agent。v2 的鲜明能力是稳定的任务契约、源码证据、六维 DFX 并发分析、黑盒优先转译和可验收报告，而不是角色切换。

## 退役与保留

| v1 项 | v2 处理 |
| --- | --- |
| `dev-expert`、`troubleshooter`、`test-designer` | 退役为可切换入口；有效能力下沉为内部步骤/skills |
| 多场景导航菜单 | 退役；只保留 MR 回归和模块全量分析入口 |
| `code-excavator` 单壳 | 演进为共享源码分析底座，服务六个 DFX 子 Agent |
| lenses、methods、playbooks、templates | 保留并重组为可按证据装配的方法库 |
| Evidence、manifest、audit、断点恢复 | 保留，迁移为任务契约、风险卡、检查点账本和自动验收 |
| `source/inputs/workspace/outputs/projects/assets` | 迁移至项目内 Git 忽略的 `pangea-data/`；需提供兼容探测或一次性迁移 |
| 旧 registry/workflow 定义与报告格式 | 不作为 v2 的运行事实；后续实现时替换 |

## 实施顺序

1. 创建 `pangea-data` 目录协议、Run manifest、检查点账本、资料导入和安全仓库更新。
2. 重写活动 Agent、命令与编排，留下唯一用户入口及两个工作流。
3. 建立共享 C/C++ 分析底座、六个内部 DFX 子 Agent和统一风险卡。
4. 完成 MR 回归，再完成模块全量分析、`--fast` 与资源规格/泄漏专项。
5. 落地 `report.md` 与离线 HTML、中文状态、工具能力探测和自动验收。

## 不迁移的行为

- 不恢复多个用户可选专家或按角色分流。
- 不把日志定位、抓包、用例评审、缺陷单单独开放为首版工作流。
- 不在源码仓写配置、索引、缓存或分析输出。
- 不让 Agent 自动联网安装工具、使用容器、提交代码或解决 Git 冲突。
- 不把 Agent 推断自动升级为跨 Run 的团队事实。

## 验收门

迁移完成需通过公开隐藏缺陷集、mini-storage fixture、只读源码、脏仓跳过更新、Run 恢复、临时目录清理和离线 HTML 验证。补丁测试策略在两个首版工作流稳定后进入下一迭代。
