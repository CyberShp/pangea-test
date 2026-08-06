from __future__ import annotations

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
