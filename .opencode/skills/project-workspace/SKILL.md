---
name: project-workspace
description: 自动识别当前 PANGEA 项目、源码、输入目录、工作空间和输出目录，并启动隔离的托管工作流
---

# Project Workspace

1. 项目空间固定在仓库根目录：`source/ inputs/ workspace/ outputs/ projects/ assets/`。
2. 源码目录只读，绝不在 `source/<repo>/` 内创建 PANGEA 文件。
3. 先执行 `python -m tooling.pangea_cli project show` 获取当前项目；不存在时，依据用户意图初始化项目，而不是反复询问四个目录。
4. 输入材料放入 `inputs/<project>/` 后，执行 `input scan` 自动分类。
5. 正式任务执行 `workflow start`，由项目配置自动推导源码、工作区、输出目录与资料。
6. 单点请求不启动工作流；出现“全量、系统性、正式交付、SFMEA、结合设计/覆盖率”等信号时自动升格。
7. `/project-*` 与 `/analyze-module` 仅是调试快捷入口，不能要求普通用户记忆。
