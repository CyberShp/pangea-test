"""Frozen compact native-output protocol for bounded R2 leaf roles."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from runtime import fragment_runtime

VERSION = "compact-analysis-v1"
CANDIDATE_PROTOCOL_VERSION = "2.0"
CANDIDATE_INSTRUCTIONS = ("Return one strict analysis_fragment JSON object. Analyse only injected source ranges; "
                          "include every obligation disposition.")
WORKER_ITEM_LIMIT = 29
WORKER_CLAIM_LIMIT = 1
AUDITOR_CLAIM_LIMIT = 100
NATIVE_OUTPUT_BYTE_LIMIT = 4096
INPUT_BYTE_LIMIT = 180000
ANALYSIS_WORKER_CALL_LIMIT = 29
SEMANTIC_AUDITOR_CALL_LIMIT = 2
FIXED_MODEL_CALL_CAPS = {"intake": 4, "resume": 1, "report-auditor": 1, "finalize": 1}
MAX_MODEL_CALLS = 40
EVIDENCE_MIN_BYTES = 12
EVIDENCE_MAX_BYTES = 32
SEMANTIC_MIN_BYTES = 12
SEMANTIC_MAX_BYTES = 32
CLAIM_MIN_BYTES = 12
CLAIM_MAX_BYTES = 32
RICH_RISK_LIMIT = 1
FAMILY_CODES = dict(zip("fbsrceov", fragment_runtime.CONTRIBUTION_FAMILIES))
PRIORITIES = {"P0", "P1", "P2", "P3"}
RISK_SEVERITIES = {"High", "Critical"}
OUTCOMES = {"A", "N"}
_GENERIC = {"looks good", "no issue", "nothing found", "analyzed", "not applicable", "unknown"}


class CompactProtocolError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def analysis_fragment_schema() -> str:
    """Return the exact frozen managed-fragment schema bytes as text."""
    return (Path(__file__).resolve().parents[1]/"schemas/analysis-fragment.schema.json").read_text(encoding="utf-8")


def _query_contract() -> dict[str, Any]:
    return {"evidence_bytes": [EVIDENCE_MIN_BYTES, EVIDENCE_MAX_BYTES],
            "semantic_bytes": [SEMANTIC_MIN_BYTES, SEMANTIC_MAX_BYTES],
            "claim_bytes": [CLAIM_MIN_BYTES, CLAIM_MAX_BYTES],
            "claim_limit": WORKER_CLAIM_LIMIT,"rich_risk_limit": RICH_RISK_LIMIT,
            "claim_forms": {
                "C": ["C", "family", "priority", "action", "summary", "control", "oracle"],
                "R": ["R", "severity", "action", "summary", "trigger", "propagation",
                      "impact", "observation", "recovery", "control", "oracle"],
            },
            "families": FAMILY_CODES,"priorities": sorted(PRIORITIES),
            "risk_severities": sorted(RISK_SEVERITIES),"outcomes": sorted(OUTCOMES)}


def _validate_ordinal_map(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if (not isinstance(value, dict) or set(value) != {"version", "items", "actions"}
            or value.get("version") != VERSION or not isinstance(value.get("items"), list)
            or not value["items"] or not isinstance(value.get("actions"), list)):
        raise CompactProtocolError("frozen compact ordinal map is invalid")
    seen_inventory_ids: set[str] = set()
    for ordinal, row in enumerate(value["items"]):
        if (not isinstance(row, dict)
                or set(row) != {"ordinal", "inventory_id", "path", "line_start", "line_end"}
                or type(row.get("ordinal")) is not int or row["ordinal"] != ordinal
                or not isinstance(row.get("inventory_id"), str)
                or not re.fullmatch(r"INV-[0-9a-f]{16}", row["inventory_id"])
                or row["inventory_id"] in seen_inventory_ids
                or not isinstance(row.get("path"), str) or not row["path"]
                or type(row.get("line_start")) is not int or type(row.get("line_end")) is not int
                or row["line_start"] < 1 or row["line_end"] < row["line_start"]):
            raise CompactProtocolError("frozen compact ordinal map is invalid")
        seen_inventory_ids.add(row["inventory_id"])
    seen_obligation_ids: set[str] = set()
    for ordinal, row in enumerate(value["actions"]):
        if (not isinstance(row, dict)
                or set(row) != {"ordinal", "obligation_id", "inventory_ordinal", "action"}
                or type(row.get("ordinal")) is not int or row["ordinal"] != ordinal
                or not isinstance(row.get("obligation_id"), str)
                or not re.fullmatch(r"OBL-[0-9a-f]{16}", row["obligation_id"])
                or row["obligation_id"] in seen_obligation_ids
                or type(row.get("inventory_ordinal")) is not int
                or row["inventory_ordinal"] not in range(len(value["items"]))
                or not isinstance(row.get("action"), str) or not row["action"]):
            raise CompactProtocolError("frozen compact ordinal map is invalid")
        seen_obligation_ids.add(row["obligation_id"])
    return value["items"], value["actions"]


def _coalesced_selected_ranges(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = sorted(
        ({"inventory_ids": [row["inventory_id"]], "path": row["path"],
          "line_start": row["line_start"], "line_end": row["line_end"]} for row in items),
        key=lambda row: (row["path"], row["line_start"], row["line_end"], row["inventory_ids"]),
    )
    merged: list[dict[str, Any]] = []
    for row in normalized:
        if (merged and merged[-1]["path"] == row["path"]
                and row["line_start"] <= merged[-1]["line_end"] + 1):
            merged[-1]["line_end"] = max(merged[-1]["line_end"], row["line_end"])
            merged[-1]["inventory_ids"] = sorted(merged[-1]["inventory_ids"] + row["inventory_ids"])
        else:
            merged.append(dict(row))
    return merged


def validate_candidate_static(candidate: Any, *, expected_ordinal_map: Any | None = None) -> None:
    """Validate snapshot-independent frozen candidate and injected projections."""
    required={"protocol_version","output_schema","output_schema_sha256","instructions","context_pack",
              "skill_receipts","injected","compact_context","compact_context_sha256","ordinal_map",
              "ordinal_map_sha256","adapter_version"}
    schema=analysis_fragment_schema()
    if (not isinstance(candidate,dict) or set(candidate)!=required
            or candidate.get("protocol_version")!=CANDIDATE_PROTOCOL_VERSION
            or candidate.get("instructions")!=CANDIDATE_INSTRUCTIONS
            or candidate.get("output_schema")!=schema
            or candidate.get("output_schema_sha256")!=hashlib.sha256(schema.encode()).hexdigest()
            or candidate.get("adapter_version")!=VERSION
            or not isinstance(candidate.get("compact_context"),dict)
            or candidate.get("compact_context_sha256")!=digest(candidate["compact_context"])
            or len(canonical_bytes(candidate["compact_context"]))>INPUT_BYTE_LIMIT
            or not isinstance(candidate.get("ordinal_map"),dict)
            or candidate.get("ordinal_map_sha256")!=digest(candidate["ordinal_map"])
            or not isinstance(candidate.get("context_pack"),dict)
            or not isinstance(candidate.get("skill_receipts"),list)
            or not isinstance(candidate.get("injected"),dict)):
        raise CompactProtocolError("frozen compact candidate closure is invalid")
    mapping_items,mapping_actions=_validate_ordinal_map(candidate["ordinal_map"])
    if expected_ordinal_map is not None:
        _validate_ordinal_map(expected_ordinal_map)
        if candidate["ordinal_map"]!=expected_ordinal_map:
            raise CompactProtocolError("frozen compact ordinal map closure is invalid")
    compact=candidate["compact_context"];pack=candidate["context_pack"]
    if (set(compact)!={"v","f","s","k","i","q"} or type(compact.get("v")) is not int or compact["v"]!=1
            or compact.get("f")!=pack.get("fragment_id") or compact.get("q")!=_query_contract()
            or not isinstance(compact.get("s"),list) or not isinstance(compact.get("k"),list)
            or not isinstance(compact.get("i"),list)):
        raise CompactProtocolError("frozen compact context closure is invalid")
    source_lines:dict[tuple[str,int],str]={};source_ordinals=[]
    for row in compact["s"]:
        if (not isinstance(row,list) or len(row)!=5 or type(row[0]) is not int or not isinstance(row[1],str)
                or type(row[2]) is not int or type(row[3]) is not int or row[2]<1 or row[3]<row[2]
                or not isinstance(row[4],str)):
            raise CompactProtocolError("frozen compact source projection is invalid")
        lines=row[4].split("\n")
        if len(lines)!=row[3]-row[2]+1 or row[0] in source_ordinals:
            raise CompactProtocolError("frozen compact source projection is invalid")
        source_ordinals.append(row[0])
        for offset,text in enumerate(lines):
            key=(row[1],row[2]+offset)
            if key in source_lines and source_lines[key]!=text:
                raise CompactProtocolError("frozen compact source projection is inconsistent")
            source_lines[key]=text
    item_ordinals=[]
    for row in compact["i"]:
        if (not isinstance(row,list) or len(row)!=2 or type(row[0]) is not int or not isinstance(row[1],list)
                or row[0] in item_ordinals or any(not isinstance(action,list) or len(action)!=2
                                                  or type(action[0]) is not int or not isinstance(action[1],str)
                                                  for action in row[1])):
            raise CompactProtocolError("frozen compact item projection is invalid")
        item_ordinals.append(row[0])
    if item_ordinals!=source_ordinals or item_ordinals!=sorted(item_ordinals) or not item_ordinals:
        raise CompactProtocolError("frozen compact source/item ordinal closure is invalid")
    item_by_ordinal={row["ordinal"]:row for row in mapping_items}
    if any(ordinal not in item_by_ordinal for ordinal in item_ordinals):
        raise CompactProtocolError("frozen compact item selection is invalid")
    selected_items=[item_by_ordinal[ordinal] for ordinal in item_ordinals]
    expected_actions={ordinal:[] for ordinal in item_ordinals}
    action_by_ordinal={action["ordinal"]:action for action in mapping_actions}
    for action in mapping_actions:
        if action["inventory_ordinal"] in expected_actions:
            expected_actions[action["inventory_ordinal"]].append([action["ordinal"],action["action"]])
    for source,item in zip(compact["s"],selected_items):
        if source[:4]!=[item["ordinal"],item["path"],item["line_start"],item["line_end"]]:
            raise CompactProtocolError("frozen compact source mapping projection is invalid")
    for row in compact["i"]:
        if row[1]!=expected_actions[row[0]]:
            raise CompactProtocolError("frozen compact action mapping projection is invalid")
    expected_obligation_ids=[action_by_ordinal[action[0]]["obligation_id"]
                             for row in compact["i"] for action in row[1]]
    expected_ranges=_coalesced_selected_ranges(selected_items)
    if pack.get("allowed_ranges")!=expected_ranges or pack.get("obligation_ids")!=expected_obligation_ids:
        raise CompactProtocolError("frozen compact selected pack projection is invalid")
    identity_fields=(pack.get("run_id"),pack.get("repository"),pack.get("commit"))
    if any(not isinstance(value,str) or not value for value in identity_fields):
        raise CompactProtocolError("frozen compact fragment identity is invalid")
    expected_fragment_id=fragment_identity(*identity_fields,candidate["ordinal_map"],item_ordinals)
    if pack.get("fragment_id")!=expected_fragment_id or compact["f"]!=expected_fragment_id:
        raise CompactProtocolError("frozen compact fragment identity is invalid")
    ranges=expected_ranges
    expected_sources=[]
    for window in ranges:
        if (not isinstance(window,dict) or set(window)!={"inventory_ids","path","line_start","line_end"}
                or not isinstance(window["inventory_ids"],list) or not window["inventory_ids"]
                or not isinstance(window["path"],str) or type(window["line_start"]) is not int
                or type(window["line_end"]) is not int or window["line_start"]<1
                or window["line_end"]<window["line_start"]):
            raise CompactProtocolError("frozen compact source ranges are invalid")
        try: text="\n".join(source_lines[(window["path"],line)]
                            for line in range(window["line_start"],window["line_end"]+1))
        except KeyError as exc: raise CompactProtocolError("compact context cannot reconstruct injected source") from exc
        expected_sources.append({"path":window["path"],"line_start":window["line_start"],
                                 "line_end":window["line_end"],"inventory_ids":window["inventory_ids"],
                                 "sha256":hashlib.sha256(text.encode()).hexdigest(),"text":text})
    skill_rows={}
    for row in compact["k"]:
        if (not isinstance(row,list) or len(row)!=3 or any(not isinstance(value,str) or not value for value in row)
                or row[0] in skill_rows):
            raise CompactProtocolError("frozen compact skill projection is invalid")
        skill_rows[row[0]]=row
    if list(skill_rows)!=sorted(skill_rows): raise CompactProtocolError("frozen compact skill order is invalid")
    receipts={}
    for receipt in candidate["skill_receipts"]:
        if (not isinstance(receipt,dict) or not isinstance(receipt.get("receipt_id"),str)
                or not isinstance(receipt.get("skill_id"),str) or not isinstance(receipt.get("version"),str)
                or not isinstance(receipt.get("content_sha256"),str) or receipt["receipt_id"] in receipts):
            raise CompactProtocolError("frozen compact skill receipt projection is invalid")
        receipts[receipt["receipt_id"]]=receipt
    refs=pack.get("skill_receipts")
    if not isinstance(refs,list): raise CompactProtocolError("frozen compact skill refs are invalid")
    expected_skills=[];seen_refs=[]
    for ref in refs:
        if (not isinstance(ref,dict) or set(ref)!={"receipt_id","artifact_sha256","version","content_sha256"}
                or ref.get("receipt_id") in seen_refs or ref.get("receipt_id") not in receipts):
            raise CompactProtocolError("frozen compact skill refs are invalid")
        receipt=receipts[ref["receipt_id"]];skill=skill_rows.get(receipt["skill_id"])
        if (skill is None or skill[1]!=receipt["version"] or hashlib.sha256(skill[2].encode()).hexdigest()!=receipt["content_sha256"]
                or ref["artifact_sha256"]!=digest(receipt) or ref["version"]!=receipt["version"]
                or ref["content_sha256"]!=receipt["content_sha256"]):
            raise CompactProtocolError("frozen compact skill binding is invalid")
        expected_skills.append({"receipt_id":receipt["receipt_id"],"skill_id":receipt["skill_id"],
                                "version":receipt["version"],"content_sha256":receipt["content_sha256"],
                                "sha256":receipt["content_sha256"],"text":skill[2]})
        seen_refs.append(ref["receipt_id"])
    if set(receipts)!=set(seen_refs) or set(skill_rows)!={receipt["skill_id"] for receipt in receipts.values()}:
        raise CompactProtocolError("frozen compact skill denominator is invalid")
    if candidate["injected"]!={"sources":expected_sources,"skills":expected_skills}:
        raise CompactProtocolError("frozen compact injected projection is invalid")


def fragment_identity(run_id: str, repository: str, commit: str,
                      mapping: Mapping[str, Any], item_ordinals: list[int]) -> str:
    preimage={"version":VERSION,"run_id":run_id,"repository":repository,"commit":commit,
              "ordinal_map_sha256":digest(mapping),"item_ordinals":list(item_ordinals)}
    return "frag-"+hashlib.sha256(canonical_bytes(preimage)).hexdigest()[:16]


def _bounded_text(value: Any, minimum: int, maximum: int) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    encoded = value.encode()
    normalized=" ".join(value.casefold().split())
    while normalized and (normalized[0].isspace() or unicodedata.category(normalized[0]).startswith("P")):
        normalized=normalized[1:]
    while normalized and (normalized[-1].isspace() or unicodedata.category(normalized[-1]).startswith("P")):
        normalized=normalized[:-1]
    normalized=" ".join(normalized.split())
    return minimum <= len(encoded) <= maximum and normalized not in _GENERIC


def ordinal_map(inventory: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
    items = sorted(inventory["items"], key=lambda row: row["inventory_id"])
    item_ordinals = {row["inventory_id"]: index for index, row in enumerate(items)}
    actions = sorted(ledger["obligations"], key=lambda row: row["obligation_id"])
    return {
        "version": VERSION,
        "items": [{"ordinal": index, "inventory_id": row["inventory_id"], "path": row["path"],
                   "line_start": row["line_start"], "line_end": row["line_end"]}
                  for index, row in enumerate(items)],
        "actions": [{"ordinal": index, "obligation_id": row["obligation_id"],
                     "inventory_ordinal": item_ordinals[row["inventory_id"]], "action": row["action"]}
                    for index, row in enumerate(actions)],
    }


def compact_context(inventory: Mapping[str, Any], ledger: Mapping[str, Any], snapshot: Path,
                    item_ordinals: list[int], mapping: Mapping[str, Any], fragment_id: str,
                    skills: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    item_by_ordinal = {row["ordinal"]: row for row in mapping["items"]}
    actions: dict[int, list[list[Any]]] = {ordinal: [] for ordinal in item_ordinals}
    for row in mapping["actions"]:
        if row["inventory_ordinal"] in actions:
            actions[row["inventory_ordinal"]].append([row["ordinal"], row["action"]])
    sources = []
    for ordinal in item_ordinals:
        row = item_by_ordinal[ordinal]
        lines = (snapshot / row["path"]).read_text(errors="replace").splitlines() or [""]
        sources.append([ordinal, row["path"], row["line_start"], row["line_end"],
                        "\n".join(lines[row["line_start"] - 1:row["line_end"]])])
    inventory_by_id={row["inventory_id"]:row for row in inventory["items"]}; skills=skills or {}
    trigger_ids=sorted({skill for ordinal in item_ordinals
                        for skill in inventory_by_id[item_by_ordinal[ordinal]["inventory_id"]].get("storage_skill_triggers",[])})
    if any(skill not in skills for skill in trigger_ids):
        raise CompactProtocolError("compact context lacks a required trusted skill")
    skill_rows=[[skill,skills[skill]["version"],skills[skill]["content"]] for skill in trigger_ids]
    value = {"v": 1, "f": fragment_id, "s": sources, "k":skill_rows,
             "i": [[ordinal, actions[ordinal]] for ordinal in item_ordinals],"q":_query_contract()}
    if len(canonical_bytes(value)) > INPUT_BYTE_LIMIT:
        raise CompactProtocolError("compact worker input exceeds frozen bound")
    return value


def maximum_native_output(context: Mapping[str, Any], mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Construct the canonical heaviest output permitted for one context."""
    item_map={row["ordinal"]:row for row in mapping["items"]}
    item_ordinals=[row[0] for row in context["i"]]
    action_ordinals=sorted(action[0] for row in context["i"] for action in row[1])
    claims:list[list[Any]]=[]
    if action_ordinals:
        claims.append(["R","Critical",action_ordinals[0],*(["R"*CLAIM_MAX_BYTES]*8)])
        for family,ordinal in zip(tuple(FAMILY_CODES)[:WORKER_CLAIM_LIMIT-RICH_RISK_LIMIT],action_ordinals[1:]):
            claims.append(["C",family,"P0",ordinal,"C"*CLAIM_MAX_BYTES,
                           "K"*CLAIM_MAX_BYTES,"O"*CLAIM_MAX_BYTES])
    return {"v":1,
            "i":[[ordinal,"E"*EVIDENCE_MAX_BYTES] for ordinal in item_ordinals],
            "a":[[ordinal,"A","S"*SEMANTIC_MAX_BYTES] for ordinal in action_ordinals],
            "c":claims}


def _normalize_native_text(value: Any) -> str:
    if not isinstance(value,str):
        raise CompactProtocolError("compact native text is invalid")
    normalized=" ".join(value.split())
    if len(normalized.encode())<EVIDENCE_MIN_BYTES:
        raise CompactProtocolError("compact native text cannot be safely normalized")
    encoded=normalized.encode()
    if len(encoded)>EVIDENCE_MAX_BYTES:
        safe_prefix=None
        for index in range(1,len(normalized)+1):
            prefix=normalized[:index]
            if len(prefix.encode())>EVIDENCE_MAX_BYTES: break
            if (index==len(normalized) or not normalized[index-1].isalnum()
                    or not normalized[index].isalnum()):
                candidate=prefix.rstrip()
                if len(candidate.encode())>=EVIDENCE_MIN_BYTES: safe_prefix=candidate
        if safe_prefix is None:
            raise CompactProtocolError("compact native text has no safe token boundary")
        normalized=safe_prefix
    if not _bounded_text(normalized,EVIDENCE_MIN_BYTES,EVIDENCE_MAX_BYTES):
        raise CompactProtocolError("compact native text cannot be safely normalized")
    return normalized


def canonicalize_native(native: Any, compact: Mapping[str, Any]) -> dict[str, Any]:
    """Replay raw leaf JSON into the one frozen adapter-native representation."""
    if (not isinstance(native,dict) or set(native)!={"v","i","a","c"}
            or type(native.get("v")) is not int or native["v"]!=1
            or not isinstance(native.get("i"),list) or not isinstance(native.get("a"),list)
            or not isinstance(native.get("c"),list)
            or len(canonical_bytes(native))>NATIVE_OUTPUT_BYTE_LIMIT):
        raise CompactProtocolError("compact native output closure is invalid")
    if not isinstance(compact,Mapping) or not isinstance(compact.get("i"),list):
        raise CompactProtocolError("compact context item closure is invalid")
    expected_items=[row[0] for row in compact["i"]]
    expected_actions=sorted(action[0] for row in compact["i"] for action in row[1])
    item_rows:dict[int,str|None]={}
    for row in native["i"]:
        if (not isinstance(row,list) or len(row)!=2 or type(row[0]) is not int
                or row[0] not in expected_items or row[0] in item_rows or not isinstance(row[1],str)):
            raise CompactProtocolError("compact native item rows are invalid")
        try: item_rows[row[0]]=_normalize_native_text(row[1])
        except CompactProtocolError: item_rows[row[0]]=None
    action_rows:dict[int,list[Any]]={}
    for row in native["a"]:
        if (not isinstance(row,list) or len(row)!=3 or type(row[0]) is not int
                or row[0] not in expected_actions or row[0] in action_rows or row[1] not in OUTCOMES):
            raise CompactProtocolError("compact native action rows are invalid")
        action_rows[row[0]]=[row[0],row[1],_normalize_native_text(row[2])]
    if set(action_rows)!=set(expected_actions):
        raise CompactProtocolError("compact native action projection is incomplete")
    actions_by_item={row[0]:sorted(action[0] for action in row[1]) for row in compact["i"]}
    derived_items=[ordinal for ordinal in expected_items if item_rows.get(ordinal) is None]
    if len(derived_items)>2 or len(derived_items)*10>len(expected_items):
        raise CompactProtocolError("compact native derived item limit exceeded")
    canonical_items=[]
    for ordinal in expected_items:
        evidence=item_rows.get(ordinal)
        if evidence is None:
            item_actions=actions_by_item[ordinal]
            if not item_actions: raise CompactProtocolError("compact native item projection is incomplete")
            evidence=action_rows[item_actions[0]][2]
        canonical_items.append([ordinal,evidence])
    if len(native["c"])>WORKER_CLAIM_LIMIT:
        raise CompactProtocolError("compact native collection shape is invalid")
    claims=[]
    for row in native["c"]:
        if not isinstance(row,list) or not row:
            raise CompactProtocolError("compact native claim shape is invalid")
        if row[0]=="C" and len(row)==7:
            text_indexes=range(4,7)
        elif row[0]=="R" and len(row)==11:
            text_indexes=range(3,11)
        else:
            raise CompactProtocolError("compact native claim shape is invalid")
        changed=list(row)
        for index in text_indexes: changed[index]=_normalize_native_text(changed[index])
        claims.append(changed)
    canonical={"v":1,"i":canonical_items,"a":[action_rows[ordinal] for ordinal in expected_actions],"c":claims}
    if len(canonical_bytes(canonical))>NATIVE_OUTPUT_BYTE_LIMIT:
        raise CompactProtocolError("canonical compact native output exceeds frozen byte limit")
    return canonical


def capacity_plan(inventory: Mapping[str, Any], ledger: Mapping[str, Any], snapshot: Path,
                  run_id: str, skills: Mapping[str, Mapping[str, str]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    mapping = ordinal_map(inventory, ledger)
    group_count=(len(mapping["items"])+WORKER_ITEM_LIMIT-1)//WORKER_ITEM_LIMIT
    if group_count > ANALYSIS_WORKER_CALL_LIMIT:
        raise CompactProtocolError("inventory exceeds frozen analysis-worker call closure")
    action_by_item: dict[int, list[int]] = {row["ordinal"]: [] for row in mapping["items"]}
    for row in mapping["actions"]:
        action_by_item[row["inventory_ordinal"]].append(row["ordinal"])
    groups=[[] for _ in range(group_count)]; loads=[0 for _ in range(group_count)]
    for ordinal in sorted(action_by_item,key=lambda value:(-len(action_by_item[value]),value)):
        eligible=[index for index,group in enumerate(groups) if len(group)<WORKER_ITEM_LIMIT]
        if not eligible: raise CompactProtocolError("compact item packing closure is invalid")
        index=min(eligible,key=lambda value:(loads[value],len(groups[value]),value))
        groups[index].append(ordinal);loads[index]+=len(action_by_item[ordinal])
    groups=[sorted(group) for group in groups]
    contexts = []
    for index, group in enumerate(groups):
        fid=fragment_identity(run_id,inventory["repository"],inventory["commit"],mapping,group)
        contexts.append({"fragment_id": fid, "item_ordinals": group,
                         "action_ordinals": [value for ordinal in group for value in action_by_item[ordinal]],
                         "compact_context": compact_context(inventory, ledger, snapshot, group, mapping, fid,skills)})
    max_input=max((len(canonical_bytes(row["compact_context"])) for row in contexts),default=0)
    max_output=max((len(canonical_bytes(maximum_native_output(row["compact_context"],mapping)))
                    for row in contexts),default=0)
    if max_output>NATIVE_OUTPUT_BYTE_LIMIT:
        raise CompactProtocolError("canonical maximum native output exceeds frozen byte limit")
    worst_claims = len(contexts) * WORKER_CLAIM_LIMIT
    auditor_calls = (worst_claims + AUDITOR_CLAIM_LIMIT - 1) // AUDITOR_CLAIM_LIMIT
    worst = sum(FIXED_MODEL_CALL_CAPS.values()) + len(contexts) + auditor_calls
    if auditor_calls > SEMANTIC_AUDITOR_CALL_LIMIT or worst > MAX_MODEL_CALLS:
        raise CompactProtocolError("compact role/model-call closure exceeds frozen budget")
    plan = {"version": VERSION,"repository":inventory["repository"],"commit":inventory["commit"],
            "ordinal_map_sha256": digest(mapping),
            "inventory_items": len(mapping["items"]), "obligations": len(mapping["actions"]),
            "analysis_worker_calls": len(contexts), "analysis_worker_call_limit": ANALYSIS_WORKER_CALL_LIMIT,
            "semantic_auditor_calls": auditor_calls, "semantic_auditor_call_limit": SEMANTIC_AUDITOR_CALL_LIMIT,
            "fixed_model_call_caps": dict(FIXED_MODEL_CALL_CAPS), "worst_model_calls": worst,
            "max_model_calls": MAX_MODEL_CALLS, "native_output_byte_limit": NATIVE_OUTPUT_BYTE_LIMIT,
            "input_byte_limit": INPUT_BYTE_LIMIT,"maximum_compact_input_bytes":max_input,
            "maximum_native_output_bytes":max_output}
    return plan, contexts


def expand_native(native: Any, compact: Mapping[str, Any], mapping: Mapping[str, Any],
                  pack: Mapping[str, Any],
                  worker_instance: str = "analysis-worker") -> dict[str, Any]:
    if (not isinstance(native, dict) or set(native) != {"v", "i", "a", "c"}
            or type(native.get("v")) is not int or native.get("v") != 1):
        raise CompactProtocolError("compact native output closure is invalid")
    if not isinstance(compact, Mapping) or type(compact.get("v")) is not int or compact.get("v") != 1:
        raise CompactProtocolError("compact context version is invalid")
    if len(canonical_bytes(native)) > NATIVE_OUTPUT_BYTE_LIMIT:
        raise CompactProtocolError("compact native output exceeds frozen byte limit")
    expected_items = [row[0] for row in compact["i"]]
    expected_actions = sorted(action[0] for row in compact["i"] for action in row[1])
    expected_fragment=fragment_identity(pack.get("run_id"),pack.get("repository"),pack.get("commit"),mapping,expected_items)
    if compact.get("f")!=expected_fragment or pack.get("fragment_id")!=expected_fragment:
        raise CompactProtocolError("compact fragment identity binding is invalid")
    item_rows, action_rows, claims = native["i"], native["a"], native["c"]
    if (not isinstance(item_rows, list) or not isinstance(action_rows, list) or not isinstance(claims, list)
            or len(claims) > WORKER_CLAIM_LIMIT):
        raise CompactProtocolError("compact native collection shape is invalid")
    if [row[0] for row in item_rows if isinstance(row, list) and row] != expected_items or len(item_rows) != len(expected_items):
        raise CompactProtocolError("compact item projection is incomplete")
    if [row[0] for row in action_rows if isinstance(row, list) and row] != expected_actions or len(action_rows) != len(expected_actions):
        raise CompactProtocolError("compact action projection is incomplete")
    item_map = {row["ordinal"]: row for row in mapping["items"]}
    action_map = {row["ordinal"]: row for row in mapping["actions"]}
    summaries: dict[int, str] = {}
    for row in item_rows:
        if (not isinstance(row, list) or len(row) != 2 or row[0] not in item_map
                or not _bounded_text(row[1], EVIDENCE_MIN_BYTES, EVIDENCE_MAX_BYTES)):
            raise CompactProtocolError("compact item evidence is invalid")
        summaries[row[0]] = row[1]
    semantics: dict[int, tuple[str, str]] = {}
    for row in action_rows:
        if (not isinstance(row, list) or len(row) != 3 or row[0] not in action_map or row[1] not in OUTCOMES
                or not _bounded_text(row[2], SEMANTIC_MIN_BYTES, SEMANTIC_MAX_BYTES)):
            raise CompactProtocolError("compact action result is invalid")
        semantics[row[0]] = (row[1], row[2])
    source_text={row[0]:row[4] for row in compact["s"]}
    facts=[]; dispositions=[]; fact_by_action={}
    for ordinal in expected_actions:
        action=action_map[ordinal]; item=item_map[action["inventory_ordinal"]]
        text=source_text[item["ordinal"]]
        key=[action["obligation_id"], item["inventory_id"], item["line_start"], item["line_end"]-item["line_start"]+1]
        fact={"obligation_id":key[0],"inventory_id":key[1],"path":item["path"],"line_start":key[2],"line_count":key[3],
              "excerpt_sha256":hashlib.sha256(text.encode()).hexdigest(),
              "evidence":summaries[item["ordinal"]]+": "+semantics[ordinal][1]}
        facts.append(fact); fact_by_action[ordinal]=key
        if semantics[ordinal][0]=="A":
            dispositions.append({"obligation_id":key[0],"outcome":"analyzed","reason":semantics[ordinal][1]})
        else:
            dispositions.append({"obligation_id":key[0],"outcome":"not_applicable","reason":semantics[ordinal][1],
                                 "boundary":semantics[ordinal][1],"counterevidence_fact_keys":[key]})
    contributions={family:[] for family in fragment_runtime.CONTRIBUTION_FAMILIES}; risks=[]
    seen_claims=set(); rich_risks=0
    for row in claims:
        if not isinstance(row,list) or not row:
            raise CompactProtocolError("compact claim is invalid")
        if row[0]=="C":
            if (len(row)!=7 or row[1] not in FAMILY_CODES or row[2] not in PRIORITIES or row[3] not in semantics
                    or any(not _bounded_text(value,CLAIM_MIN_BYTES,CLAIM_MAX_BYTES) for value in row[4:])
                    or ("C",row[1],row[3]) in seen_claims):
                raise CompactProtocolError("compact contribution claim is invalid")
            seen_claims.add(("C",row[1],row[3])); action=action_map[row[3]]
            payload={"priority":row[2],"obligation_id":action["obligation_id"],"fact_keys":[fact_by_action[row[3]]],
                     "summary":row[4],"controls":[row[5]],"oracles":[row[6]]}
            claim={"contribution_id":"C-"+digest(payload)[:16],**payload}
            contributions[FAMILY_CODES[row[1]]].append(claim)
        elif row[0]=="R":
            rich_risks+=1
            if (rich_risks>RICH_RISK_LIMIT or len(row)!=11 or row[1] not in RISK_SEVERITIES
                    or row[2] not in semantics
                    or any(not _bounded_text(value,CLAIM_MIN_BYTES,CLAIM_MAX_BYTES) for value in row[3:])
                    or ("R",row[2]) in seen_claims):
                raise CompactProtocolError("compact rich-risk claim is invalid")
            seen_claims.add(("R",row[2])); action=action_map[row[2]]
            payload={"severity":row[1],"obligation_id":action["obligation_id"],
                     "fact_keys":[fact_by_action[row[2]]],"summary":row[3],"trigger":row[4],
                     "propagation":row[5],"impact":row[6],"observation":row[7],"recovery":row[8],
                     "control":row[9],"oracle":row[10]}
            risks.append({"risk_id":"R-"+digest(payload)[:16],**payload})
        else:
            raise CompactProtocolError("compact claim kind is invalid")
    return {"artifact_type":"analysis_fragment","schema_version":"2.0","worker_instance":worker_instance,
            "run_id":pack["run_id"],"fragment_id":pack["fragment_id"],"context_pack_sha256":digest(pack),
            "obligation_ids":list(pack["obligation_ids"]),"skill_receipt_ids":[row["receipt_id"] for row in pack["skill_receipts"]],
            "facts":facts,"contributions":contributions,"risk_cards":risks,"dispositions":dispositions,"unresolved":[],
            "usage":{"output_tokens":1,"finish_reason":"stop","valid_json":True}}
