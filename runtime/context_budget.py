"""Immutable, source-deduplicated context packs with a 10% safety margin."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime.obligation_ledger import validate as validate_ledger
from runtime.source_inventory import _SKILLS, validate as validate_inventory

SCHEMA_VERSION = "1.0"
MODEL_CONTEXT_TOKENS = 200_000
INPUT_BUDGET_LIMIT = 180_000
OUTPUT_RESERVED_TOKENS = 4_096
SYSTEM_PROMPT_RESERVED_TOKENS = 12_000
TOOL_SCHEMAS_RESERVED_TOKENS = 12_000
PROTOCOL_RESERVED_TOKENS = 4_096
ESTIMATOR_ID = "utf8-json-byte-upper-bound"
ESTIMATOR_VERSION = "1.0"


class ContextError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def token_estimate(value: Any) -> int:
    """Conservative bound for byte-fallback tokenizers, including CJK text.

    A UTF-8 byte is charged as one token instead of the unsafe bytes/4
    heuristic.  This intentionally overestimates ASCII and never discounts
    multibyte CJK input.
    """
    return max(1, len(_canonical(value)))


def _budget_receipt(pack: dict[str, Any], injected: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Reach a fixed point because the versioned receipt is part of the envelope."""
    probe = deepcopy(pack)
    injected_tokens = token_estimate(injected)
    for _ in range(16):
        envelope_tokens = token_estimate(probe)
        input_tokens = (envelope_tokens + injected_tokens + SYSTEM_PROMPT_RESERVED_TOKENS
                        + TOOL_SCHEMAS_RESERVED_TOKENS + PROTOCOL_RESERVED_TOKENS)
        receipt = {
            "estimator_id": ESTIMATOR_ID, "estimator_version": ESTIMATOR_VERSION,
            "envelope_upper_bound_tokens": envelope_tokens,
            "injected_upper_bound_tokens": injected_tokens,
            "system_prompt_reserved_tokens": SYSTEM_PROMPT_RESERVED_TOKENS,
            "tool_schemas_reserved_tokens": TOOL_SCHEMAS_RESERVED_TOKENS,
            "protocol_reserved_tokens": PROTOCOL_RESERVED_TOKENS,
            "output_reserved_tokens": OUTPUT_RESERVED_TOKENS,
            "total_context_upper_bound_tokens": input_tokens + OUTPUT_RESERVED_TOKENS,
        }
        if probe.get("input_budget_tokens") == input_tokens and probe.get("budget_receipt") == receipt:
            return input_tokens, receipt
        probe["input_budget_tokens"] = input_tokens
        probe["budget_receipt"] = receipt
    raise ContextError("budget estimator did not converge")


def _validate_trusted_skills(trusted_skills: Any) -> None:
    if not isinstance(trusted_skills, dict) or any(skill not in _SKILLS for skill in trusted_skills):
        raise ContextError("invalid trusted skill registry")
    for skill_id, value in trusted_skills.items():
        if (not isinstance(skill_id, str) or not isinstance(value, dict)
                or set(value) != {"version", "content"}
                or any(not isinstance(value[key], str) or not value[key].strip() for key in ("version", "content"))):
            raise ContextError("invalid trusted skill registry")


def _coalesce_ranges(ranges: Any) -> list[dict[str, Any]]:
    if not isinstance(ranges, list) or not ranges:
        raise ContextError("missing source ranges")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for value in ranges:
        if not isinstance(value, dict):
            raise ContextError("bad range")
        if set(value) == {"inventory_id", "path", "line_start", "line_end"}:
            inventory_ids = [value["inventory_id"]]
        elif set(value) == {"inventory_ids", "path", "line_start", "line_end"}:
            inventory_ids = value["inventory_ids"]
        else:
            raise ContextError("bad range")
        if (not isinstance(inventory_ids, list) or not inventory_ids
                or any(not isinstance(item, str) or not re.fullmatch(r"INV-[0-9a-f]{16}", item) for item in inventory_ids)
                or len(inventory_ids) != len(set(inventory_ids))
                or any(item in seen_ids for item in inventory_ids)
                or not isinstance(value["path"], str) or not value["path"]
                or type(value["line_start"]) is not int or type(value["line_end"]) is not int
                or value["line_start"] < 1 or value["line_end"] < value["line_start"]):
            raise ContextError("bad range")
        seen_ids.update(inventory_ids)
        normalized.append({"inventory_ids": sorted(inventory_ids), "path": value["path"],
                           "line_start": value["line_start"], "line_end": value["line_end"]})
    normalized.sort(key=lambda row: (row["path"], row["line_start"], row["line_end"], row["inventory_ids"]))
    merged: list[dict[str, Any]] = []
    for row in normalized:
        if (merged and merged[-1]["path"] == row["path"]
                and row["line_start"] <= merged[-1]["line_end"] + 1):
            merged[-1]["line_end"] = max(merged[-1]["line_end"], row["line_end"])
            merged[-1]["inventory_ids"] = sorted(merged[-1]["inventory_ids"] + row["inventory_ids"])
        else:
            merged.append(dict(row))
    return merged


def _injected(pack: dict[str, Any], trusted_root: str, receipts: list[dict[str, Any]],
              trusted_skills: dict[str, dict[str, str]]) -> dict[str, Any]:
    root = Path(trusted_root).resolve()
    sources: list[dict[str, Any]] = []
    for window in pack["allowed_ranges"]:
        lines = (root / window["path"]).read_text(errors="replace").splitlines() or [""]
        source = "\n".join(lines[window["line_start"] - 1:window["line_end"]])
        sources.append({
            "path": window["path"], "line_start": window["line_start"],
            "line_end": window["line_end"], "inventory_ids": window["inventory_ids"],
            "sha256": hashlib.sha256(source.encode()).hexdigest(), "text": source,
        })
    selected = {receipt["receipt_id"]: receipt for receipt in receipts}
    skills: list[dict[str, Any]] = []
    for ref in pack["skill_receipts"]:
        receipt = selected[ref["receipt_id"]]
        content = trusted_skills[receipt["skill_id"]]["content"]
        skills.append({"receipt_id": receipt["receipt_id"], "skill_id": receipt["skill_id"],
                       "sha256": hashlib.sha256(content.encode()).hexdigest(), "text": content})
    return {"sources": sources, "skills": skills}


def _digests(injected: dict[str, Any]) -> dict[str, Any]:
    return {kind: [{key: value for key, value in row.items() if key != "text"} for row in rows]
            for kind, rows in injected.items()}


def build(inventory: dict[str, Any], ledger: dict[str, Any], trusted_root: str,
          obligation_ids: list[str], ranges: list[dict[str, Any]], receipts: list[dict[str, Any]],
          trusted_skills: dict[str, dict[str, str]], run_id: str, fragment_id: str) -> dict[str, Any]:
    validate_inventory(inventory, trusted_root)
    validate_ledger(ledger, inventory, trusted_root)
    _validate_trusted_skills(trusted_skills)
    refs = [{"receipt_id": receipt["receipt_id"], "artifact_sha256": digest(receipt),
             "version": receipt["version"], "content_sha256": receipt["content_sha256"]}
            for receipt in receipts]
    pack = {
        "artifact_type": "context_pack", "schema_version": SCHEMA_VERSION,
        "worker": "analysis-worker", "run_id": run_id, "fragment_id": fragment_id,
        "repository": inventory["repository"], "commit": inventory["commit"],
        "snapshot_sha256": inventory["snapshot_sha256"], "inventory_sha256": digest(inventory),
        "ledger_sha256": digest(ledger), "obligation_ids": list(obligation_ids),
        "allowed_ranges": _coalesce_ranges(ranges), "skill_receipts": refs,
        "content_digests": {"sources": [], "skills": []}, "input_budget_tokens": 0,
        "output_budget_tokens": OUTPUT_RESERVED_TOKENS, "budget_receipt": {},
    }
    injected = _injected(pack, trusted_root, receipts, trusted_skills)
    pack["content_digests"] = _digests(injected)
    pack["input_budget_tokens"], pack["budget_receipt"] = _budget_receipt(pack, injected)
    validate(pack, inventory, ledger, trusted_root, receipts, trusted_skills)
    return pack


def validate(pack: dict[str, Any], inventory: dict[str, Any], ledger: dict[str, Any],
             trusted_root: str, receipts: list[dict[str, Any]],
             trusted_skills: dict[str, dict[str, str]]) -> None:
    validate_inventory(inventory, trusted_root)
    validate_ledger(ledger, inventory, trusted_root)
    _validate_trusted_skills(trusted_skills)
    required = {"artifact_type", "schema_version", "worker", "run_id", "fragment_id", "repository", "commit",
                "snapshot_sha256", "inventory_sha256", "ledger_sha256", "obligation_ids", "allowed_ranges",
                "skill_receipts", "content_digests", "input_budget_tokens", "output_budget_tokens", "budget_receipt"}
    if (not isinstance(pack, dict) or set(pack) != required
            or pack.get("artifact_type") != "context_pack" or pack.get("schema_version") != SCHEMA_VERSION
            or pack.get("worker") != "analysis-worker"
            or any(not isinstance(pack.get(key), str) or not pack[key].strip() for key in ("run_id", "fragment_id"))):
        raise ContextError("invalid context pack")
    for key in ("repository", "commit", "snapshot_sha256"):
        if pack.get(key) != inventory[key]:
            raise ContextError("snapshot binding mismatch")
    if pack.get("inventory_sha256") != digest(inventory) or pack.get("ledger_sha256") != digest(ledger):
        raise ContextError("stale pack")
    known_rows = {row["obligation_id"]: row for row in ledger["obligations"]}
    obligation_ids = pack.get("obligation_ids")
    if (not isinstance(obligation_ids, list) or not obligation_ids
            or len(obligation_ids) != len(set(obligation_ids))
            or any(not isinstance(oid, str) or oid not in known_rows for oid in obligation_ids)):
        raise ContextError("invalid obligations")
    if pack.get("allowed_ranges") != _coalesce_ranges(pack.get("allowed_ranges")):
        raise ContextError("ranges are not canonical")
    root = Path(trusted_root).resolve()
    items = {item["inventory_id"]: item for item in inventory["items"]}
    selected_items = {known_rows[oid]["inventory_id"] for oid in obligation_ids}
    ranged_items: set[str] = set()
    for window in pack["allowed_ranges"]:
        path = root / window["path"]
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ContextError("range path missing") from exc
        if path.is_symlink() or root not in resolved.parents or window["path"] not in inventory["scope"]:
            raise ContextError("range escape")
        lines = path.read_text(errors="replace").splitlines() or [""]
        if window["line_end"] > len(lines):
            raise ContextError("range outside source")
        for inventory_id in window["inventory_ids"]:
            if inventory_id not in selected_items or inventory_id in ranged_items:
                raise ContextError("irrelevant or duplicate inventory binding")
            item = items[inventory_id]
            if (item["path"] != window["path"]
                    or not (window["line_start"] <= item["line_start"] <= item["line_end"] <= window["line_end"])):
                raise ContextError("range does not cover item")
            ranged_items.add(inventory_id)
    if ranged_items != selected_items:
        raise ContextError("selected obligation lacks source range")
    if not isinstance(receipts, list) or any(not isinstance(receipt, dict) for receipt in receipts):
        raise ContextError("invalid receipts")
    for receipt in receipts:
        from runtime.fragment_runtime import validate_receipt
        validate_receipt(receipt, inventory, ledger, trusted_skills)
    known_receipts = {receipt.get("receipt_id"): receipt for receipt in receipts}
    if None in known_receipts or len(known_receipts) != len(receipts):
        raise ContextError("duplicate receipt artifacts")
    refs = pack.get("skill_receipts")
    if not isinstance(refs, list):
        raise ContextError("invalid receipt references")
    ref_ids: list[str] = []
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {"receipt_id", "artifact_sha256", "version", "content_sha256"}
                or ref["receipt_id"] in ref_ids or ref["receipt_id"] not in known_receipts):
            raise ContextError("forged receipt reference")
        receipt = known_receipts[ref["receipt_id"]]
        validate_receipt(receipt, inventory, ledger, trusted_skills)
        if (ref["artifact_sha256"] != digest(receipt) or ref["version"] != receipt["version"]
                or ref["content_sha256"] != receipt["content_sha256"]):
            raise ContextError("receipt binding mismatch")
        if not set(receipt["obligation_ids"]) <= set(obligation_ids):
            raise ContextError("receipt includes unselected obligation")
        ref_ids.append(ref["receipt_id"])
    if set(known_receipts) != set(ref_ids):
        raise ContextError("receipt parameter set differs from context pack")
    required_pairs = Counter((oid, skill) for oid in obligation_ids
                             for skill in items[known_rows[oid]["inventory_id"]]["storage_skill_triggers"])
    delivered_pairs = Counter((oid, receipt["skill_id"]) for receipt in receipts
                              for oid in receipt["obligation_ids"])
    if delivered_pairs != required_pairs or any(count != 1 for count in delivered_pairs.values()):
        raise ContextError("receipt coverage mismatch")
    injected = _injected(pack, trusted_root, receipts, trusted_skills)
    if pack.get("content_digests") != _digests(injected):
        raise ContextError("content digest mismatch")
    expected, budget_receipt = _budget_receipt(pack, injected)
    if (type(pack.get("input_budget_tokens")) is not int or pack["input_budget_tokens"] != expected
            or pack.get("budget_receipt") != budget_receipt
            or not 0 < expected <= INPUT_BUDGET_LIMIT
            or budget_receipt["total_context_upper_bound_tokens"] > MODEL_CONTEXT_TOKENS
            or type(pack.get("output_budget_tokens")) is not int
            or pack["output_budget_tokens"] != OUTPUT_RESERVED_TOKENS):
        raise ContextError("untrusted budget")
