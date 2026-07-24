# Claude Code 适配层（预留空壳）

> 本目录是 PANGEA-TEST 的 Claude Code 载体适配层，**M1 仅预留**，实现后补。
> 设计原则：`core/`（平台无关纯 md 资产）跨载体**零改动**复用；各载体只重写本目录这类薄壳。

## 待补内容（后续里程碑）
- `.claude/agents/*.md`：将 `adapters/opencode/.opencode/agents/*.md` 的 opencode frontmatter 映射为 Claude Code agent 格式（字段名有差异，届时按 Claude Code 官方文档重写薄壳，正文引用同一份 `core/` 资产）。
- `.claude/skills/<name>/SKILL.md`：将 `core/scenarios/` 按需包装为 Claude Code skill。

## 映射对照（待落地）
| opencode | Claude Code |
|---|---|
| `.opencode/agents/<name>.md` frontmatter (`mode`/`permission`/…) | `.claude/agents/<name>.md`（字段名按 CC 文档）|
| `core/scenarios/*.md`（族 agent 引用）| `.claude/skills/<name>/SKILL.md` 或同样纯 md 引用 |
| `core/` 全部资产 | **零改动复用** |

> 注意：`core/` 中所有对文件的相对路径引用需在各适配层保持可解析（部署时通过软链或构建脚本把 `core/` 暴露到 agent 可读位置）。详见 docs/architecture.md §5.3、内网待办 T-1。
