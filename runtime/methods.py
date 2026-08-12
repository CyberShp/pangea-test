"""Evidence-gated loading of non-normative analysis methods."""
from __future__ import annotations

from pathlib import Path
from typing import Any


METHOD_FILES = {
    "c-cpp-analysis": ".opencode/skills/c-cpp-analysis/SKILL.md",
    "analysis-depth": ".opencode/skills/analysis-depth/SKILL.md",
    "storage-spdk": ".opencode/skills/storage-spdk/SKILL.md",
    "storage-iscsi": ".opencode/skills/storage-iscsi/SKILL.md",
    "storage-resource-recovery": ".opencode/skills/storage-resource-recovery/SKILL.md",
    "storage-nvme-cli": ".opencode/skills/storage-nvme-cli/SKILL.md",
    "storage-nvmeof": ".opencode/skills/storage-nvmeof/SKILL.md",
    "storage-destructive-cli": ".opencode/skills/storage-destructive-cli/SKILL.md",
    "dfx-analysis": ".opencode/skills/dfx-analysis/SKILL.md",
    "risk-card": ".opencode/skills/risk-card/SKILL.md",
    "sfmea": ".opencode/skills/sfmea/SKILL.md",
    "test-design": ".opencode/skills/test-design/SKILL.md",
}
CODE_ROOT = Path(__file__).resolve().parents[1]

RESOURCE_SIGNALS = (
    "alloc", "calloc", "realloc", "free", "get", "put", "ref", "destroy", "fini",
    "socket", "thread", "poller", "queue", "pool", "mempool", "buffer", "close",
)


class MethodError(RuntimeError):
    pass


def _load(root: Path, name: str, reason: str, *, include_checklist: bool = False) -> dict[str, str]:
    relative = METHOD_FILES[name]
    path = CODE_ROOT / relative
    try:
        instructions = path.read_text(encoding="utf-8")
        checklist = path.parent / "references" / "analysis-checklist.md"
        if include_checklist and checklist.is_file():
            instructions += "\n\n# Embedded analysis checklist\n\n" + checklist.read_text(encoding="utf-8")
    except OSError as exc:
        raise MethodError(f"cannot load analysis method {name}: {exc}") from exc
    if not instructions.strip():
        raise MethodError(f"analysis method {name} is empty")
    return {"name": name, "reason": reason, "instructions": instructions}


def select_slice_methods(root: Path, task: dict[str, str], sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Select the smallest useful method set from frozen source signals."""
    source_text = "\n".join(
        [source["path"] for source in sources]
        + [line["text"] for source in sources for line in source.get("lines", [])]
    ).lower()
    text = "\n".join(
        [task["repository"], task["scope"], task["target"]]
        + [source_text]
    ).lower()
    selected = [
        _load(root, "c-cpp-analysis", "首版 C/C++ 模块分析共同底座"),
        _load(root, "analysis-depth", "完整语义对象与证据边界共同方法"),
    ]
    if "spdk" in text or "spdk_" in text:
        deep = any(signal in source_text for signal in (
            "spdk_thread_send_msg", "spdk_poller", "spdk_bdev", "io_channel", "spdk_nvmf", "mempool", "dma",
        ))
        selected.append(_load(root, "storage-spdk", "仓库、符号或头文件命中 SPDK", include_checklist=deep))
    if "iscsi" in text:
        deep = any(signal in source_text for signal in (
            "iscsi_login", "session", "connection", "cmdsn", "statsn", "itt", "ttt", "chap", "digest", "pdu",
        ))
        selected.append(_load(root, "storage-iscsi", "目标、路径或源码符号命中 iSCSI", include_checklist=deep))
    if any(signal in text for signal in ("nvmeof", "nvme-of", "nvmf", "spdk_nvmf")):
        selected.append(_load(root, "storage-nvmeof", "目标、路径或源码符号命中 NVMe-oF/NVMf", include_checklist=True))
    if "nvme" in text and any(signal in text for signal in ("cmd_handler", "parse_and_open", "entry(", "plugin")):
        selected.append(_load(root, "storage-nvme-cli", "源码命中 nvme-cli 分发、解析或插件信号", include_checklist=True))
    if any(signal in text for signal in ("sanitize", "firmware activate", "namespace delete", "format_nvm", "--force")):
        selected.append(_load(root, "storage-destructive-cli", "源码命中破坏性存储 CLI 的分发或门禁信号", include_checklist=True))
    if any(signal in text for signal in RESOURCE_SIGNALS):
        selected.append(_load(root, "storage-resource-recovery", "源码命中资源、生命周期或恢复信号", include_checklist=True))
    return selected


def dfx_methods(root: Path) -> list[dict[str, str]]:
    return [_load(root, "dfx-analysis", "模块语义冻结后的六维复核")]


def risk_methods(root: Path) -> list[dict[str, str]]:
    return [
        _load(root, "risk-card", "风险候选判定与测试可行性分离"),
        _load(root, "sfmea", "confirmed risk 的模块级失效模式分析"),
    ]


def adjudication_methods(root: Path) -> list[dict[str, str]]:
    return [_load(root, "risk-card", "风险候选成立性判定与测试可行性分离")]


def test_methods(root: Path) -> list[dict[str, str]]:
    return [_load(root, "test-design", "从 confirmed risk 与冻结行为派生可执行测试")]


design_methods = risk_methods
