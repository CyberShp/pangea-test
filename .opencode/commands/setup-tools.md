---
description: 探测并规划 PANGEA-TEST 可选分析工具的受控安装
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前必须已有本会话成功的 portable preflight。禁止 `cd`、`cd /d`、`&&`、`||`、`;` 和手工盘符转换；一次工具调用只启动一个进程，并使用 preflight 返回的 `project_root` 作为结构化 workdir。preflight 未解析出唯一项目根时停止并询问用户，不得扫描盘符或猜测目录。


先自主选择当前环境实际可执行的 Python 3.9+ 解释器（使用 `preflight.python_executable` 返回的当前解释器），并在本次操作中保持一致。使用该解释器运行 `-m tooling.pangea_cli tool probe` 获取 GitNexus、文档转换和静态工具的实际能力与版本；再运行 `-m tooling.pangea_cli tool setup-plan $ARGUMENTS` 仅输出用户明确指定工具的受控来源建议。两条命令都不安装、不联网、不使用容器。

1. 显示 `[梳理中 (._.)]`，先运行能力检测并列出缺失工具、来源、版本、用途和对分析深度的影响。
2. 将 `setup-plan` 的结果作为待用户确认的操作建议，而不是安装结果。安装只能由用户在已确认的内网或系统软件源中另行完成。
3. 可选工具包括 GitNexus、clang-tidy、cppcheck、Semgrep；CodeQL 仅作为独立高成本可选项，不假定可由 npm/pip 安装。
4. 用户完成外部安装或启用后，重新运行 `tool probe` 复检版本和最小可用命令，记录实际成功、失败和降级路径。不得修改任何分析目标代码仓。
