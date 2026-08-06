---
name: project-workspace
description: 管理 PANGEA-TEST 的个人数据目录、只读仓库、Run 与正式报告
---

# PANGEA-TEST 工作空间

1. 个人数据位于项目根目录的 `pangea-data/`：`inbox/`、`library/`、`repositories/`、`runs/`、`indexes/`、`registry/`。
2. 代码仓位于 `pangea-data/repositories/`，严格只读；不得在代码仓写入 PANGEA 文件、执行提交、暂存、重置、强制切换或自动解决冲突。
3. 每个 Run 使用 `pangea-data/runs/<run-id>/`，其中 `manifest.json`、`checkpoints/`、`evidence/`、`internal/`、`tmp/` 和 `final/` 分开保存。正式交付为 `final/report.md` 与 `final/report.html`。
4. 用户资料先进入 `pangea-data/inbox/`，再以原文件只读副本、转换 Markdown、资源锚点和 Catalog 记录进入 `library/`；不得移动、重命名或修改用户原文件。
5. new session 发现新资料或代码更新时，先做增量检查。仓库只有干净且可安全快进时才可建议 `git pull`；无运行层支持时不执行 pull。
6. 正式分析必须先建立任务契约和 Run，再从锁定的材料、版本和代码范围继续；单点问答可以不创建 Run。
