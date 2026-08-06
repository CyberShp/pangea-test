from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


workspace_runtime = r'''"""Portable, no-guess PANGEA workspace preflight."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from runtime.process_runtime import run_text

ROOT_MARKERS = (
    ".opencode",
    "runtime/runctl.py",
    "tooling/pangea_cli/__main__.py",
    "registry/scenarios.json",
)
SHELL_OPERATORS = {"&&", "||", ";"}


class WorkspaceResolutionError(RuntimeError):
    def __init__(self, message: str, *, checked_paths: list[str] | None = None) -> None:
        super().__init__(message)
        self.checked_paths = checked_paths or []


def _marker_missing(root: Path) -> list[str]:
    return [marker for marker in ROOT_MARKERS if not (root / marker).exists()]


def validate_project_root(value: str | Path, *, platform_name: str | None = None) -> Path:
    raw = str(value)
    effective_platform = (platform_name or os.name).lower()
    if effective_platform in {"nt", "windows"} and re.match(r"^/[A-Za-z]/", raw):
        raise WorkspaceResolutionError(
            f"Windows 下拒绝 MSYS 风格盘符路径: {raw}；请使用原生绝对路径，例如 D:\\\\path\\\\pangea-test"
        )
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceResolutionError(f"项目根目录不存在或不可解析: {candidate}") from exc
    if not resolved.is_dir():
        raise WorkspaceResolutionError(f"项目根路径不是目录: {resolved}")
    missing = _marker_missing(resolved)
    if missing:
        raise WorkspaceResolutionError(
            "指定目录不是 PANGEA-TEST 项目根目录；缺少标记: " + ", ".join(missing),
            checked_paths=[str(resolved)],
        )
    return resolved


def locate_project_root(
    *,
    explicit: str | Path | None = None,
    start: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    package_root: str | Path | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    environment = os.environ if env is None else env
    checked: list[str] = []
    if explicit is not None:
        root = validate_project_root(explicit, platform_name=platform_name)
        return {"project_root": root, "root_source": "explicit", "checked_paths": [str(root)]}
    configured = environment.get("PANGEA_ROOT")
    if configured:
        root = validate_project_root(configured, platform_name=platform_name)
        return {"project_root": root, "root_source": "environment", "checked_paths": [str(root)]}

    origin = Path(start or Path.cwd()).expanduser()
    try:
        origin = origin.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceResolutionError(f"当前工作目录不可解析: {origin}") from exc
    candidates = [origin, *origin.parents]
    for candidate in candidates:
        checked.append(str(candidate))
        if not _marker_missing(candidate):
            return {"project_root": candidate, "root_source": "cwd_or_parent", "checked_paths": checked}

    if package_root is not None:
        package = Path(package_root).expanduser().resolve()
        if str(package) not in checked:
            checked.append(str(package))
            if not _marker_missing(package):
                return {"project_root": package, "root_source": "package_root", "checked_paths": checked}
    raise WorkspaceResolutionError(
        "当前目录及其父目录中未找到 PANGEA-TEST 根标记；禁止扫描盘符或猜测相似目录",
        checked_paths=checked,
    )


def shell_family(env: Mapping[str, str] | None = None, *, platform_name: str | None = None) -> str:
    environment = os.environ if env is None else env
    effective_platform = (platform_name or os.name).lower()
    if effective_platform in {"nt", "windows"}:
        if environment.get("PSModulePath") or environment.get("POWERSHELL_DISTRIBUTION_CHANNEL"):
            return "powershell"
        return "windows"
    shell = Path(environment.get("SHELL", "")).name.lower()
    return shell or "posix"


def build_preflight_steps(project_root: Path, python_executable: str) -> list[tuple[str, list[str]]]:
    return [
        ("session_prepare", [python_executable, "-m", "tooling.pangea_cli", "data", "--root", str(project_root), "session-prepare"]),
        ("library_refresh_hints", [python_executable, "-m", "tooling.pangea_cli", "library", "refresh-hints"]),
        ("tool_probe", [python_executable, "-m", "tooling.pangea_cli", "tool", "probe"]),
        ("index_all", [python_executable, "-m", "tooling.pangea_cli", "index", "all"]),
    ]


def _parse_step_output(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_output": text}


def _known_repositories(repository_root: Path) -> list[str]:
    if not repository_root.is_dir():
        return []
    return sorted(path.name for path in repository_root.iterdir() if path.is_dir() and not path.is_symlink())


def run_preflight(
    *,
    explicit_root: str | Path | None = None,
    start: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    python_executable: str | None = None,
    runner: Callable[..., Any] = run_text,
    platform_name: str | None = None,
) -> dict[str, Any]:
    process_cwd = Path(start or Path.cwd()).expanduser()
    executable = str(Path(python_executable or sys.executable).resolve())
    base: dict[str, Any] = {
        "status": "workspace_unresolved",
        "platform": platform.system().lower(),
        "shell_family": shell_family(env, platform_name=platform_name),
        "python_executable": executable,
        "python_version": platform.python_version(),
        "process_cwd": str(process_cwd),
        "project_root": None,
        "root_source": None,
        "checked_paths": [],
        "data_root": None,
        "repository_root": None,
        "known_repositories": [],
        "step_results": {},
        "step_errors": {},
        "allowed_next_actions": ["ask_user_for_project_root"],
    }
    try:
        located = locate_project_root(
            explicit=explicit_root,
            start=process_cwd,
            env=env,
            package_root=Path(__file__).resolve().parents[1],
            platform_name=platform_name,
        )
    except WorkspaceResolutionError as exc:
        base["checked_paths"] = exc.checked_paths
        base["step_errors"]["workspace"] = {"type": type(exc).__name__, "message": str(exc)}
        return base

    project_root = Path(located["project_root"])
    data_root = project_root / "pangea-data"
    repository_root = data_root / "repositories"
    base.update({
        "status": "ready",
        "project_root": str(project_root),
        "root_source": located["root_source"],
        "checked_paths": located["checked_paths"],
        "data_root": str(data_root),
        "repository_root": str(repository_root),
        "known_repositories": _known_repositories(repository_root),
        "allowed_next_actions": ["draft_contract"],
    })

    for name, command in build_preflight_steps(project_root, executable):
        if any(part in SHELL_OPERATORS for part in command):
            base["step_errors"][name] = {"type": "UnsafeCommand", "message": "preflight 子命令不得包含 shell 连接符"}
            continue
        try:
            result = runner(command, cwd=str(project_root))
        except BaseException as exc:
            base["step_errors"][name] = {"type": type(exc).__name__, "message": str(exc) or "子命令执行失败"}
            continue
        if result.returncode:
            base["step_errors"][name] = {
                "type": "CommandFailed",
                "returncode": result.returncode,
                "message": (result.stderr or result.stdout or "子命令执行失败").strip(),
                "command": command,
            }
        else:
            base["step_results"][name] = _parse_step_output(result.stdout or "")

    session = base["step_results"].get("session_prepare")
    if isinstance(session, dict):
        base["data_root"] = session.get("data_root", base["data_root"])
        known = session.get("known_repositories")
        if isinstance(known, list):
            base["known_repositories"] = known
    else:
        base["known_repositories"] = _known_repositories(repository_root)
    if base["step_errors"]:
        base["status"] = "degraded"
        base["allowed_next_actions"] = ["inspect_step_errors"]
        if "session_prepare" not in base["step_errors"]:
            base["allowed_next_actions"].append("draft_contract")
    return base
'''
write("runtime/workspace_runtime.py", workspace_runtime)

preflightctl = r'''from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime import workspace_runtime


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA portable no-guess preflight")
    parser.add_argument("--root")
    parser.add_argument("--start")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    result = workspace_runtime.run_preflight(
        explicit_root=args.root,
        start=Path(args.start) if args.start else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ready", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
'''
write("tooling/pangea_cli/preflightctl.py", preflightctl)

main = read("tooling/pangea_cli/__main__.py")
main = replace_once(
    main,
    '            "data", "report", "tool", "library", "repo", "index",\n',
    '            "data", "report", "tool", "library", "repo", "index", "preflight",\n',
    "CLI area",
)
main = replace_once(
    main,
    '        "index": "indexctl",\n',
    '        "index": "indexctl", "preflight": "preflightctl",\n',
    "CLI module",
)
write("tooling/pangea_cli/__main__.py", main)

# Make session preparation return path fallbacks and isolate every step.
data = read("runtime/data_runtime.py")
old = '''def session_prepare(root: Path, stale_hours: int = 24) -> dict[str, Any]:
    workspace = ensure_layout(root)
    inbox = scan_inbox(root)
    document_import = convert_catalog(root)
    step_errors: dict[str, dict[str, str]] = {}
    try:
        repositories = safe_pull_repositories(root)
    except (DataRuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        repositories = []
        step_errors["repositories"] = {
            "type": type(exc).__name__,
            "message": str(exc) or "仓库准备失败",
        }
    return {
        "data_root": str(workspace),
        "inbox": inbox,
        "document_import": document_import,
        "repositories": repositories,
        "incomplete_runs": incomplete_runs(root),
        "tmp_cleanup": cleanup_stale_tmp(root, stale_hours),
        "workspace_inventory": workspace_inventory(root),
        "step_errors": step_errors,
    }
'''
new = '''def session_prepare(root: Path, stale_hours: int = 24) -> dict[str, Any]:
    project_root = Path(root).expanduser().resolve()
    workspace = ensure_layout(project_root)
    repository_root = workspace / "repositories"
    step_errors: dict[str, dict[str, str]] = {}

    def capture(name: str, action: Any, fallback: Any) -> Any:
        try:
            return action()
        except (DataRuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
            step_errors[name] = {"type": type(exc).__name__, "message": str(exc) or f"{name} 失败"}
            return fallback

    inbox = capture("inbox", lambda: scan_inbox(project_root), {"added": 0, "changed": 0, "count": 0})
    document_import = capture(
        "document_import", lambda: convert_catalog(project_root),
        {"catalog": None, "converted": 0, "reused": 0, "pending": 0, "skipped": 0, "count": 0},
    )
    repositories = capture("repositories", lambda: safe_pull_repositories(project_root), [])
    unfinished = capture("incomplete_runs", lambda: incomplete_runs(project_root), [])
    cleanup = capture("tmp_cleanup", lambda: cleanup_stale_tmp(project_root, stale_hours), {"removed": [], "stale_hours": stale_hours})
    inventory = capture(
        "workspace_inventory", lambda: workspace_inventory(project_root),
        {"locations": {"repositories": str(repository_root), "run_history": str(workspace / "runs"),
                       "formal_reports": str(workspace / "reports")},
         "formal_reports": [], "run_history": [], "legacy_reports": []},
    )
    known_repositories = sorted(path.name for path in repository_root.iterdir()
                                if path.is_dir() and not path.is_symlink())
    return {
        "current_workspace": str(Path.cwd().resolve()),
        "project_root": str(project_root),
        "data_root": str(workspace),
        "repository_root": str(repository_root),
        "known_repositories": known_repositories,
        "inbox": inbox,
        "document_import": document_import,
        "repositories": repositories,
        "incomplete_runs": unfinished,
        "tmp_cleanup": cleanup,
        "workspace_inventory": inventory,
        "step_errors": step_errors,
    }
'''
data = replace_once(data, old, new, "session prepare")
write("runtime/data_runtime.py", data)

# Replace formal command documents with one portable preflight and no shell-string composition.
initial = read(".opencode/commands/initial.md")
start = initial.index("用户参数：`$ARGUMENTS`")
header = initial[:start]
initial_body = r'''用户参数：`$ARGUMENTS`

只运行一个真实入口：

```text
<当前 Python 解释器> -m tooling.pangea_cli preflight $ARGUMENTS
```

不得先执行 `cd`，不得使用 `&&`、`||`、`;` 拼接命令，不得把 `/d/...`、`/c/...` 等 MSYS 路径手工转换为 Windows 路径。工具调用必须通过结构化 `cwd/workdir` 保持在当前项目上下文中，一次调用只启动一个进程。

以 preflight JSON 为唯一事实源：

- `project_root` 是经项目标记验证的根目录；`python_executable` 是后续命令唯一允许使用的解释器。
- `repository_root` 和 `known_repositories` 是唯一可用的仓库定位依据。
- `status: workspace_unresolved` 时停止全部仓库搜索、索引、Run 创建和源码分析；只向用户请求真实项目根目录。
- `status: degraded` 时读取 `step_errors`，不得把失败步骤解释成仓库不存在，也不得猜测其他盘符目录。
- 禁止枚举 `C:\`、`D:\`、`/` 等盘符或文件系统根目录寻找“看起来像”的项目；根目录恢复只允许当前目录、其父目录、显式 `--root` 或 `PANGEA_ROOT`。
- `step_results.session_prepare.workspace_inventory` 中：`formal_reports` 是正式交付，`run_history` 是历史 Run，`legacy_reports` 是旧报告。

preflight 已按独立子进程顺序执行 session prepare、资料提示刷新、工具探测和索引；不得重复拼接运行这四条命令。只报告 JSON 中真实成功的结果。
'''
write(".opencode/commands/initial.md", header + initial_body)

for path in (".opencode/commands/module-analysis.md", ".opencode/commands/mr-regression.md", ".opencode/commands/resume-run.md", ".opencode/commands/setup-tools.md"):
    text = read(path)
    text = text.replace("python3 runtime/runctl.py", "<preflight.python_executable> runtime/runctl.py")
    text = text.replace("python3 -m tooling.pangea_cli", "<preflight.python_executable> -m tooling.pangea_cli")
    text = text.replace("Windows 通常 `python`，POSIX 通常 `python3`", "使用 `preflight.python_executable` 返回的当前解释器")
    portable = (
        "\n\n执行命令前必须已有本会话成功的 portable preflight。禁止 `cd`、`cd /d`、`&&`、`||`、`;` 和手工盘符转换；"
        "一次工具调用只启动一个进程，并使用 preflight 返回的 `project_root` 作为结构化 workdir。preflight 未解析出唯一项目根时停止并询问用户，不得扫描盘符或猜测目录。\n"
    )
    marker = "用户参数：`$ARGUMENTS`"
    if marker in text and "portable preflight" not in text:
        text = text.replace(marker, marker + portable, 1)
    write(path, text)

agent = read(".opencode/agents/pangea-test.md")
needle = "## 仓库访问与更新边界\n"
portable_policy = '''## Portable Preflight 与禁止猜测

每个新会话及正式入口必须先运行单进程 portable preflight，并只使用其 `project_root`、`python_executable`、`repository_root`、`known_repositories` 和 `step_errors`。这是执行门禁，不是展示建议。

- 禁止在命令字符串中使用 `cd`、`cd /d`、`&&`、`||` 或 `;`；一次工具调用只启动一个进程，工作目录通过工具的结构化 workdir/cwd 传递。
- 禁止将 `/d/...`、`/c/...` 等路径猜测转换成 `D:\\...`、`C:\\...`，禁止扫描盘符根目录或根据相似目录名猜项目位置。
- preflight `workspace_unresolved` 时，唯一允许动作是请用户提供真实项目根目录；不得搜索代码、调用子 Agent、创建 Run、创建 `pangea-data` 或声称仓库缺失。
- 任一子步骤失败时仍以 preflight 的稳定 JSON 为准。`project_root` 已知但某一步失败，只能报告该 `step_errors`，不得自行替换工作区。
- 后续所有 Python 命令必须使用 preflight 返回的精确 `python_executable`，不得重新猜测 `python` 或 `python3`。

'''
agent = replace_once(agent, needle, portable_policy + needle, "portable agent policy")
write(".opencode/agents/pangea-test.md", agent)

# Add Windows-first documentation without rewriting every POSIX example.
readme = read("README.md")
anchor = "## 10 分钟上手\n"
windows_note = r'''## Windows 与 PowerShell

在 Windows 上不要使用 `cd /d/... && python3 ...`。PANGEA 正式入口使用一个进程完成根目录验证和初始化：

```powershell
python -m tooling.pangea_cli preflight
```

从项目目录启动 OpenCode 后直接运行 `/initial`，Agent 不应自行切换目录。路径包含空格或中文时无需转换；项目根目录只通过当前目录/父目录标记、显式 `--root` 或 `PANGEA_ROOT` 解析。若返回 `workspace_unresolved`，系统不会创建 `pangea-data`，也不会扫描其他盘符。

'''
readme = replace_once(readme, anchor, windows_note + anchor, "README Windows")
write("README.md", readme)

# Tests.
test = r'''from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import workspace_runtime


class PortablePreflightTests(unittest.TestCase):
    @staticmethod
    def marked_root(root: Path) -> Path:
        (root / ".opencode").mkdir(parents=True)
        (root / "runtime").mkdir()
        (root / "runtime/runctl.py").write_text("# marker\n", encoding="utf-8")
        (root / "tooling/pangea_cli").mkdir(parents=True)
        (root / "tooling/pangea_cli/__main__.py").write_text("# marker\n", encoding="utf-8")
        (root / "registry").mkdir()
        (root / "registry/scenarios.json").write_text("{}\n", encoding="utf-8")
        return root

    def test_locates_only_current_directory_and_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "项目 根")
            nested = root / "a" / "b"; nested.mkdir(parents=True)
            result = workspace_runtime.locate_project_root(start=nested, package_root=None, env={})
            self.assertEqual(root.resolve(), result["project_root"])
            self.assertEqual("cwd_or_parent", result["root_source"])
            self.assertNotIn(str(Path(tmp).parent), result["checked_paths"])

    def test_explicit_wrong_root_never_creates_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "pangea_code"; wrong.mkdir()
            result = workspace_runtime.run_preflight(explicit_root=wrong, start=wrong)
            self.assertEqual("workspace_unresolved", result["status"])
            self.assertFalse((wrong / "pangea-data").exists())
            self.assertEqual(["ask_user_for_project_root"], result["allowed_next_actions"])

    def test_windows_rejects_msys_drive_path(self) -> None:
        with self.assertRaises(workspace_runtime.WorkspaceResolutionError):
            workspace_runtime.validate_project_root("/d/2026/pangea-test", platform_name="windows")

    def test_child_commands_are_argument_arrays_without_shell_operators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "中文 空格")
            steps = workspace_runtime.build_preflight_steps(root, sys.executable)
            self.assertEqual(4, len(steps))
            for _, command in steps:
                self.assertEqual(sys.executable, command[0])
                self.assertFalse(set(command) & workspace_runtime.SHELL_OPERATORS)
                self.assertNotIn("cd", command)

    def test_failure_returns_stable_paths_and_does_not_guess(self) -> None:
        calls: list[tuple[list[str], str]] = []
        def runner(command: list[str], *, cwd: str) -> subprocess.CompletedProcess[str]:
            calls.append((command, cwd))
            return subprocess.CompletedProcess(command, 2, "", "simulated failure")
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "PANGEA 项目")
            result = workspace_runtime.run_preflight(explicit_root=root, runner=runner)
            self.assertEqual("degraded", result["status"])
            self.assertEqual(str(root.resolve()), result["project_root"])
            self.assertEqual(str(root.resolve() / "pangea-data/repositories"), result["repository_root"])
            self.assertEqual(4, len(result["step_errors"]))
            self.assertTrue(all(cwd == str(root.resolve()) for _, cwd in calls))

    def test_preflight_cli_always_emits_json_for_bad_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-m", "tooling.pangea_cli", "preflight", "--root", tmp],
                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            payload = json.loads(result.stdout)
            self.assertEqual("workspace_unresolved", payload["status"])
            self.assertIn("workspace", payload["step_errors"])


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_portable_preflight.py", test)

# Extend existing isolation assertion for fallback fields.
isolation = read("tests/test_session_prepare_isolation.py")
isolation = isolation.replace(
    '        self.assertEqual([], prepared["repositories"])\n',
    '        self.assertEqual([], prepared["repositories"])\n'
    '        self.assertEqual(str(root.resolve()), prepared["project_root"])\n'
    '        self.assertEqual(str(root.resolve() / "pangea-data/repositories"), prepared["repository_root"])\n'
    '        self.assertEqual([], prepared["known_repositories"])\n',
    1,
)
write("tests/test_session_prepare_isolation.py", isolation)

# Add structural regression rules for Agent command text.
agent_test = read("tests/test_agent_v2.py")
insert = '''\n    def test_formal_commands_use_portable_preflight_and_never_compose_shell_commands(self) -> None:\n        combined = "\\n".join((COMMANDS / f"{name}.md").read_text(encoding="utf-8") for name in FORMAL_COMMANDS)\n        self.assertIn("tooling.pangea_cli preflight", combined)\n        self.assertNotIn("cd /d", combined.lower())\n        self.assertNotIn("&&", combined)\n        self.assertNotIn("python3 runtime/runctl.py", combined)\n        primary = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")\n        for rule in ("workspace_unresolved", "禁止扫描盘符", "python_executable", "一次工具调用只启动一个进程"):\n            self.assertIn(rule, primary)\n\n'''
marker = '\n    def test_primary_can_dispatch_only_internal_capabilities(self) -> None:\n'
agent_test = replace_once(agent_test, marker, insert + marker, "agent portable test")
write("tests/test_agent_v2.py", agent_test)
