from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:180]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Empty tracked directories do not disappear from the current checkout merely
# because their last file was unlinked by the refactor script.
for relative in (
    "source", "inputs", "workspace", "outputs", "projects", "runs",
    "core/modules", "core/protocols",
):
    directory = ROOT / relative
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()

# A Run with no checkpoint directory represents zero checkpoints.
replace_once(
    "runtime/runctl.py",
    '''    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_files = sorted(checkpoint_dir.iterdir(), key=lambda item: item.name)
''',
    '''    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_files = (sorted(checkpoint_dir.iterdir(), key=lambda item: item.name)
                        if checkpoint_dir.is_dir() else [])
''',
)

# Keep the implementation detail named in the structural contract while making
# it clear that the runtime, not the Agent, owns the calculation.
replace_once(
    ".opencode/skills/report-contract/SKILL.md",
    '''由运行时把完整模型原子写到 `pangea-data/runs/<run-id>/internal/report-model.json` 并返回 SHA-256。''',
    '''由运行时把完整模型原子写到 `pangea-data/runs/<run-id>/internal/report-model.json`，使用 `hashlib.sha256` 计算并返回 SHA-256。''',
)

# Existing security tests explicitly build malformed optional directories; they
# now create the parent first instead of relying on eager empty placeholders.
replace_once(
    "tests/test_data_runtime.py",
    '''        stale_active = run_dir / "tmp" / "snapshot"; stale_active.mkdir()
''',
    '''        stale_active = run_dir / "tmp" / "snapshot"; stale_active.mkdir(parents=True)
''',
)
replace_once(
    "tests/test_data_runtime.py",
    '''        stale_terminal = completed_dir / "tmp" / "old"; stale_terminal.mkdir()
        fresh_terminal = completed_dir / "tmp" / "new"; fresh_terminal.mkdir()
''',
    '''        stale_terminal = completed_dir / "tmp" / "old"; stale_terminal.mkdir(parents=True)
        fresh_terminal = completed_dir / "tmp" / "new"; fresh_terminal.mkdir()
''',
)
replace_once(
    "tests/test_data_runtime.py",
    '''        (workspace / "indexes").rmdir()
        (workspace / "indexes").symlink_to(external, target_is_directory=True)
''',
    '''        (workspace / "indexes").symlink_to(external, target_is_directory=True)
''',
)
replace_once(
    "tests/test_data_runtime.py",
    '''        (run_dir / "internal" / "audit").rmdir()
        (run_dir / "internal" / "task-contract.json").unlink()
''',
    '''        (run_dir / "internal" / "task-contract.json").unlink()
''',
)
replace_once(
    "tests/test_data_runtime.py",
    '''        markdown = workspace / "library" / "markdown" / f"{record['sha256']}.md"
        markdown.symlink_to(external)
''',
    '''        markdown = workspace / "library" / "markdown" / f"{record['sha256']}.md"
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.symlink_to(external)
''',
)
replace_once(
    "tests/test_data_runtime.py",
    '''        escaped = run_dir / "tmp" / "escaped"
        escaped.symlink_to(external, target_is_directory=True)
''',
    '''        escaped = run_dir / "tmp" / "escaped"
        escaped.parent.mkdir(parents=True)
        escaped.symlink_to(external, target_is_directory=True)
''',
)
replace_once(
    "tests/test_repository_runtime.py",
    '''        sentinel = run_two_tmp / "must-stay.txt"
        sentinel.write_text("run-two", encoding="utf-8")
''',
    '''        sentinel = run_two_tmp / "must-stay.txt"
        run_two_tmp.mkdir(parents=True)
        sentinel.write_text("run-two", encoding="utf-8")
''',
)
