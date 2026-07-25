# OpenCode Adapter 迁移说明

OpenCode 已成为 PANGEA-TEST 的直接运行入口，项目配置现位于仓库根目录：

```text
.opencode/
```

不再需要 `ln -s`、Windows Junction 或手工复制 `adapters/opencode/.opencode`。

保留本目录是为了维持 `adapters/` 的跨载体架构位置；后续仅存放 OpenCode 特有的迁移说明、生成器或兼容脚本，不再维护第二份 Agent/Command/Skill 配置。
