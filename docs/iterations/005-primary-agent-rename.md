# 迭代 005：主 Agent 重命名

## 目标

将 OpenCode 主 Agent 的身份从通用实现名 `dispatcher` 升级为产品名 `pangea-test`，让 Tab 中的入口与项目品牌保持一致。

## 变更

- `.opencode/agents/dispatcher.md` → `.opencode/agents/pangea-test.md`
- 所有 `.opencode/commands/*.md` 统一绑定 `agent: pangea-test`
- Doctor 新增 `primary_agent_identity` 检查
- README 与 Smoke 链路统一展示 `pangea-test`
- 回归测试禁止活动配置重新出现 `dispatcher` Agent ID

## 兼容性

这是 Agent ID 重命名。旧会话或个人脚本中显式引用 `dispatcher` 的内容需要改成 `pangea-test`；工作流 ID、项目 ID、资产 ID 和运行目录不受影响。
