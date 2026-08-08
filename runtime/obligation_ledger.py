"""Inventory-derived obligation ledger and atomic fragment application."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from runtime.source_inventory import validate as validate_inventory

SCHEMA_VERSION = "1.0"
FINAL = {"analyzed", "covered_by_other", "not_applicable", "blocked", "need_verify"}
_ACTIONS = {
    "source_chunk": ["cover_source"], "entrypoint": ["trace", "explain", "blackbox", "disconfirm"],
    "registration": ["trace", "explain"], "branch": ["explain", "disconfirm"],
    "state": ["state", "blackbox"], "resource": ["resource", "blackbox"],
    "concurrency": ["concurrency", "disconfirm"], "error": ["error", "blackbox"],
}
_RECEIPT_ID = re.compile(r"^SR-[0-9a-f]{16}$")


class LedgerError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _id(inventory_id: str, action: str) -> str:
    return "OBL-" + hashlib.sha256(f"{inventory_id}\0{action}".encode()).hexdigest()[:16]


def _base(item: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "obligation_id": _id(item["inventory_id"], action), "inventory_id": item["inventory_id"],
        "action": action, "status": "pending", "assigned_fragment_id": None,
        "assigned_worker_id": None, "skill_receipt_ids": [], "evidence": [],
        "disposition": None, "unresolved": [],
    }


def build(inventory: dict[str, Any], trusted_root: str) -> dict[str, Any]:
    validate_inventory(inventory, trusted_root)
    rows = sorted((_base(item, action) for item in inventory["items"]
                   for action in _ACTIONS[item["kind"]]), key=lambda row: row["obligation_id"])
    return {
        "artifact_type": "obligation_ledger", "schema_version": SCHEMA_VERSION,
        "repository": inventory["repository"], "commit": inventory["commit"],
        "snapshot_sha256": inventory["snapshot_sha256"], "inventory_sha256": digest(inventory),
        "obligations": rows,
    }


def _fact_key(fact: dict[str, Any]) -> tuple[Any, ...]:
    return (fact.get("obligation_id"), fact.get("inventory_id"), fact.get("line_start"), fact.get("line_count"))


def _validate_fact(fact: Any, row: dict[str, Any], item: dict[str, Any], root: Path) -> None:
    keys = {"obligation_id", "inventory_id", "path", "line_start", "line_count", "excerpt_sha256", "evidence"}
    if (not isinstance(fact, dict) or set(fact) != keys
            or fact["obligation_id"] != row["obligation_id"]
            or fact["inventory_id"] != row["inventory_id"] or fact["path"] != item["path"]
            or type(fact["line_start"]) is not int or type(fact["line_count"]) is not int
            or fact["line_count"] < 1
            or not isinstance(fact["excerpt_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", fact["excerpt_sha256"])
            or not isinstance(fact["evidence"], str) or not fact["evidence"].strip()):
        raise LedgerError("invalid ledger evidence")
    path = root / fact["path"]
    lines = path.read_text(errors="replace").splitlines() or [""]
    line_end=fact["line_start"]+fact["line_count"]-1
    if not (1 <= fact["line_start"] <= line_end <= len(lines)):
        raise LedgerError("unverifiable ledger evidence")
    expected = hashlib.sha256("\n".join(lines[fact["line_start"] - 1:line_end]).encode()).hexdigest()
    if expected != fact["excerpt_sha256"]:
        raise LedgerError("unverifiable ledger evidence")


def _validate_covered_graph(rows: list[dict[str, Any]]) -> None:
    by_id = {row["obligation_id"]: row for row in rows}
    for row in rows:
        disposition = row.get("disposition")
        if row.get("status") != "complete" or not isinstance(disposition, dict) or disposition.get("outcome") != "covered_by_other":
            continue
        if row["action"] == "cover_source":
            raise LedgerError("source chunks cannot be covered_by_other")
        seen: set[str] = set()
        current = row
        while True:
            oid = current["obligation_id"]
            if oid in seen:
                raise LedgerError("covered_by cycle")
            seen.add(oid)
            current_disposition = current.get("disposition")
            if current.get("status") != "complete" or not isinstance(current_disposition, dict):
                raise LedgerError("covered_by target is incomplete")
            outcome = current_disposition.get("outcome")
            if outcome == "analyzed":
                break
            if outcome != "covered_by_other":
                raise LedgerError("covered_by chain must terminate at analyzed")
            target = current_disposition.get("covered_by")
            if target not in by_id or target == oid:
                raise LedgerError("invalid covered_by target")
            target_row = by_id[target]
            if target_row["inventory_id"] != row["inventory_id"]:
                raise LedgerError("covered_by must stay within one inventory item")
            current = target_row


def validate(ledger: dict[str, Any], inventory: dict[str, Any], trusted_root: str) -> None:
    validate_inventory(inventory, trusted_root)
    required = {"artifact_type", "schema_version", "repository", "commit", "snapshot_sha256", "inventory_sha256", "obligations"}
    if (not isinstance(ledger, dict) or set(ledger) != required
            or ledger.get("artifact_type") != "obligation_ledger"
            or ledger.get("schema_version") != SCHEMA_VERSION):
        raise LedgerError("invalid ledger envelope")
    for key in ("repository", "commit", "snapshot_sha256"):
        if ledger.get(key) != inventory[key]:
            raise LedgerError("ledger snapshot binding mismatch")
    if ledger.get("inventory_sha256") != digest(inventory):
        raise LedgerError("ledger inventory digest mismatch")
    expected = {row["obligation_id"]: row for row in
                (_base(item, action) for item in inventory["items"] for action in _ACTIONS[item["kind"]])}
    rows = ledger.get("obligations")
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or any(not isinstance(row, dict) for row in rows)
            or {row.get("obligation_id") for row in rows} != set(expected)):
        raise LedgerError("missing, unknown, or duplicate obligations")
    items = {item["inventory_id"]: item for item in inventory["items"]}
    root = Path(trusted_root).resolve()
    for row in rows:
        base = expected[row["obligation_id"]]
        if set(row) != set(base) or any(row.get(key) != base[key] for key in ("obligation_id", "inventory_id", "action")):
            raise LedgerError("forged obligation")
        status, disposition = row["status"], row["disposition"]
        receipt_ids = row["skill_receipt_ids"]
        if (status not in {"pending", "assigned", "complete"} or not isinstance(receipt_ids, list)
                or len(receipt_ids) != len(set(receipt_ids))
                or any(not isinstance(value, str) or not _RECEIPT_ID.fullmatch(value) for value in receipt_ids)
                or not isinstance(row["evidence"], list) or not isinstance(row["unresolved"], list)):
            raise LedgerError("invalid obligation state")
        assigned = (isinstance(row["assigned_fragment_id"], str) and bool(row["assigned_fragment_id"].strip())
                    and isinstance(row["assigned_worker_id"], str) and bool(row["assigned_worker_id"].strip()))
        if status == "pending" and any((row["assigned_fragment_id"], row["assigned_worker_id"], receipt_ids,
                                         row["evidence"], disposition, row["unresolved"])):
            raise LedgerError("pending obligation has results")
        if status == "assigned" and (not assigned or any((receipt_ids, row["evidence"], disposition, row["unresolved"]))):
            raise LedgerError("assigned obligation has premature results")
        if status == "complete" and (not assigned or not isinstance(disposition, dict)):
            raise LedgerError("complete obligation invalid")
        item = items[row["inventory_id"]]
        seen_facts: set[tuple[Any, ...]] = set()
        for fact in row["evidence"]:
            _validate_fact(fact, row, item, root)
            key = _fact_key(fact)
            if key in seen_facts:
                raise LedgerError("duplicate ledger evidence")
            seen_facts.add(key)
        if disposition is None:
            continue
        if disposition.get("outcome") not in FINAL or not isinstance(disposition.get("reason"), str) or not disposition["reason"].strip():
            raise LedgerError("weak disposition")
        outcome = disposition["outcome"]
        expected_fields = {"outcome", "reason"}
        if outcome == "covered_by_other":
            expected_fields.add("covered_by")
        if outcome == "not_applicable":
            expected_fields |= {"boundary", "counterevidence_fact_keys"}
        if set(disposition) != expected_fields:
            raise LedgerError("outcome-specific disposition fields invalid")
        if outcome == "analyzed" and not row["evidence"]:
            raise LedgerError("analyzed requires evidence")
        if outcome == "not_applicable":
            keys = disposition["counterevidence_fact_keys"]
            if (not isinstance(disposition["boundary"], str) or not disposition["boundary"].strip()
                    or not isinstance(keys, list) or not keys
                    or any(not isinstance(key, list) or len(key) != 4
                           or not isinstance(key[0], str) or not isinstance(key[1], str)
                           or type(key[2]) is not int or type(key[3]) is not int
                           or tuple(key) not in seen_facts for key in keys)
                    or len(keys) != len({tuple(key) for key in keys})):
                raise LedgerError("weak N/A")
        if outcome in {"analyzed", "not_applicable"}:
            core_facts = (seen_facts if outcome == "analyzed"
                          else {tuple(key) for key in disposition["counterevidence_fact_keys"]})
            if item["kind"] == "source_chunk" and not any(
                    key[2] == item["line_start"] and key[3] == item["line_end"]-item["line_start"]+1 for key in core_facts):
                raise LedgerError("source chunk disposition requires whole-chunk evidence")
            if not any(max(key[2], item["line_start"]) <= min(key[2]+key[3]-1, item["line_end"])
                       for key in core_facts):
                raise LedgerError("disposition evidence does not intersect inventory item")
        if outcome in {"blocked", "need_verify"}:
            if not row["unresolved"]:
                raise LedgerError("incomplete unresolved state")
        elif row["unresolved"]:
            raise LedgerError("unexpected unresolved")
        for unresolved in row["unresolved"]:
            if (not isinstance(unresolved, dict) or set(unresolved) != {"obligation_id", "reason", "next_step"}
                    or unresolved["obligation_id"] != row["obligation_id"]
                    or any(not isinstance(unresolved[key], str) or not unresolved[key].strip() for key in ("reason", "next_step"))):
                raise LedgerError("invalid unresolved item")
        if item["storage_skill_triggers"] and not receipt_ids:
            raise LedgerError("applicable skill receipt missing")
    _validate_covered_graph(rows)


def finalize(ledger: dict[str, Any], inventory: dict[str, Any], trusted_root: str) -> None:
    validate(ledger, inventory, trusted_root)
    if any(row["status"] != "complete" or row["disposition"] is None for row in ledger["obligations"]):
        raise LedgerError("every obligation needs exactly one final disposition")


def _apply_validated(ledger: dict[str, Any], fragment: dict[str, Any], inventory: dict[str, Any],
                     trusted_root: str, receipt_map: dict[str, list[str]] | None = None) -> dict[str, Any]:
    validate(ledger, inventory, trusted_root)
    out = copy.deepcopy(ledger)
    by_id = {row["obligation_id"]: row for row in out["obligations"]}
    for disposition in fragment["dispositions"]:
        row = by_id[disposition["obligation_id"]]
        if row["status"] != "pending":
            raise LedgerError("merge conflict")
        oid = row["obligation_id"]
        row.update(
            status="complete", assigned_fragment_id=fragment["fragment_id"],
            assigned_worker_id=fragment["worker_instance"],
            skill_receipt_ids=list((receipt_map or {}).get(oid, fragment["skill_receipt_ids"])),
            evidence=copy.deepcopy([fact for fact in fragment["facts"] if fact["obligation_id"] == oid]),
            disposition=copy.deepcopy({key: value for key, value in disposition.items() if key != "obligation_id"}),
            unresolved=copy.deepcopy([item for item in fragment["unresolved"] if item["obligation_id"] == oid]),
        )
    validate(out, inventory, trusted_root)
    return out
