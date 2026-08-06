from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


Path("runtime/process_runtime.py").write_text(
    '''from __future__ import annotations

import locale
import subprocess
from collections.abc import Mapping, Sequence


def _decode_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    encodings = ("utf-8", locale.getpreferredencoding(False))
    for encoding in dict.fromkeys(encodings):
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


def run_text(
    command: Sequence[str],
    *,
    cwd: str | None = None,
    timeout: int | float | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command as bytes, then decode output without Windows reader-thread loss."""
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=False,
        check=False,
        timeout=timeout,
        env=env,
    )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        stdout=_decode_output(completed.stdout),
        stderr=_decode_output(completed.stderr),
    )
''',
    encoding="utf-8",
)

replace(
    "runtime/data_runtime.py",
    "from typing import Any\n\n\nclass DataRuntimeError",
    "from typing import Any\n\nfrom runtime.process_runtime import run_text\n\n\nclass DataRuntimeError",
)
replace(
    "runtime/data_runtime.py",
    '''        return subprocess.run(
            ["git", "-C", str(repo), *args], text=True, capture_output=True,
            check=False, timeout=GIT_TIMEOUT_SECONDS, env=env,
        )''',
    '''        return run_text(
            ["git", "-C", str(repo), *args],
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
        )''',
)
replace(
    "runtime/data_runtime.py",
    '''        inside = _git(repo, "rev-parse", "--is-inside-work-tree")
        if inside.returncode or inside.stdout.strip() != "true":''',
    '''        inside = _git(repo, "rev-parse", "--is-inside-work-tree")
        if inside.returncode or (inside.stdout or "").strip() != "true":''',
)
replace(
    "runtime/data_runtime.py",
    '''        top_level = _git(repo, "rev-parse", "--show-toplevel")
        if top_level.returncode:
            outcomes.append({"repository": name, "status": "skipped", "reason": "无法确认 Git 工作树根目录"})
            continue
        try:
            is_registered_worktree = Path(top_level.stdout.strip()).resolve() == repo.resolve()''',
    '''        top_level = _git(repo, "rev-parse", "--show-toplevel")
        top_level_output = (top_level.stdout or "").strip()
        if top_level.returncode or not top_level_output:
            outcomes.append({"repository": name, "status": "skipped", "reason": "无法确认 Git 工作树根目录"})
            continue
        try:
            is_registered_worktree = Path(top_level_output).resolve() == repo.resolve()''',
)
replace(
    "runtime/data_runtime.py",
    "if dirty.stdout.strip():",
    "if (dirty.stdout or \"\").strip():",
)
replace(
    "runtime/data_runtime.py",
    "if branch.returncode or not branch.stdout.strip():",
    "if branch.returncode or not (branch.stdout or \"\").strip():",
)
replace(
    "runtime/data_runtime.py",
    "message = (pull.stdout or pull.stderr).strip() or \"已检查更新\"",
    "message = (pull.stdout or pull.stderr or \"\").strip() or \"已检查更新\"",
)

replace(
    "runtime/index_runtime.py",
    "from runtime import data_runtime\n",
    "from runtime import data_runtime\nfrom runtime.process_runtime import run_text\n",
)
replace(
    "runtime/index_runtime.py",
    '''        return subprocess.run(list(command), cwd=cwd, text=True, capture_output=True, check=False,
                              timeout=timeout, env=environment)''',
    '''        return run_text(list(command), cwd=str(cwd) if cwd is not None else None,
                        timeout=timeout, env=environment)''',
)
replace(
    "runtime/index_runtime.py",
    '''    check = _git(runner, source, "rev-parse", "--is-inside-work-tree")
    if check.returncode or check.stdout.strip() != "true":''',
    '''    check = _git(runner, source, "rev-parse", "--is-inside-work-tree")
    if check.returncode or (check.stdout or "").strip() != "true":''',
)
replace(
    "runtime/index_runtime.py",
    '''    top_level = _git(runner, source, "rev-parse", "--show-toplevel")
    if top_level.returncode:
        raise IndexRuntimeError(f"无法确定 Git 工作树根目录: {name}")
    try:
        top_level_path = Path(top_level.stdout.strip()).resolve()''',
    '''    top_level = _git(runner, source, "rev-parse", "--show-toplevel")
    top_level_output = (top_level.stdout or "").strip()
    if top_level.returncode or not top_level_output:
        raise IndexRuntimeError(f"无法确定 Git 工作树根目录: {name}: {_git_error(top_level)}")
    try:
        top_level_path = Path(top_level_output).resolve()''',
)
replace(
    "runtime/index_runtime.py",
    "return workspace, source, revision.stdout.strip()",
    "return workspace, source, (revision.stdout or \"\").strip()",
)
replace(
    "runtime/index_runtime.py",
    "if current.stdout.strip() == source_commit:",
    "if (current.stdout or \"\").strip() == source_commit:",
)

replace(
    "runtime/capabilities.py",
    "from typing import Any, Callable, Iterable, Optional, Sequence\n",
    "from typing import Any, Callable, Iterable, Optional, Sequence\n\nfrom runtime.process_runtime import run_text\n",
)
replace(
    "runtime/capabilities.py",
    '''def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), text=True, capture_output=True, check=False)''',
    '''def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return run_text(list(command))''',
)

Path("tests/test_windows_subprocess.py").write_text(
    '''from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import data_runtime, index_runtime
from runtime import process_runtime


class WindowsSubprocessTests(unittest.TestCase):
    def test_run_text_decodes_utf8_bytes_and_normalizes_missing_streams(self) -> None:
        with patch.object(
            process_runtime.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["git"], 0, "中文路径".encode("utf-8"), None),
        ):
            result = process_runtime.run_text(["git", "rev-parse"])
        self.assertEqual("中文路径", result.stdout)
        self.assertEqual("", result.stderr)

    def test_safe_pull_treats_missing_top_level_output_as_structured_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
            repo.mkdir()
            responses = [
                subprocess.CompletedProcess(["git"], 0, "true", ""),
                subprocess.CompletedProcess(["git"], 0, None, None),
            ]
            with patch("runtime.data_runtime._git", side_effect=responses):
                result = data_runtime.safe_pull_repositories(root)
        self.assertEqual("skipped", result[0]["status"])
        self.assertEqual("无法确认 Git 工作树根目录", result[0]["reason"])

    def test_index_records_missing_top_level_output_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
            repo.mkdir()

            def runner(command, cwd, timeout):
                if command[-1] == "--is-inside-work-tree":
                    return subprocess.CompletedProcess(command, 0, "true", "")
                if command[-1] == "--show-toplevel":
                    return subprocess.CompletedProcess(command, 0, None, None)
                raise AssertionError(command)

            result = index_runtime.index_repository(root, "driver", runner=runner, which=lambda _: None)
        self.assertEqual("failed", result["status"])
        self.assertIn("无法确定 Git 工作树根目录", result["failure"])


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
