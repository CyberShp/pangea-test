"""Frozen compact native-output protocol for bounded R2 leaf roles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from runtime import fragment_runtime

VERSION = "compact-analysis-v1"
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


def _bounded_text(value: Any, minimum: int, maximum: int) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    encoded = value.encode()
    return minimum <= len(encoded) <= maximum and value.casefold() not in _GENERIC


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
             "i": [[ordinal, actions[ordinal]] for ordinal in item_ordinals],
             "q": {"evidence_bytes": [EVIDENCE_MIN_BYTES, EVIDENCE_MAX_BYTES],
                   "semantic_bytes": [SEMANTIC_MIN_BYTES, SEMANTIC_MAX_BYTES],
                   "claim_bytes": [CLAIM_MIN_BYTES, CLAIM_MAX_BYTES],
                   "claim_limit": WORKER_CLAIM_LIMIT,
                   "rich_risk_limit": RICH_RISK_LIMIT,
                   "claim_forms": {
                       "C": ["C", "family", "priority", "action", "summary", "control", "oracle"],
                       "R": ["R", "severity", "action", "summary", "trigger", "propagation",
                             "impact", "observation", "recovery", "control", "oracle"],
                   },
                   "families": FAMILY_CODES, "priorities": sorted(PRIORITIES),
                   "risk_severities": sorted(RISK_SEVERITIES), "outcomes": sorted(OUTCOMES)}}
    if len(canonical_bytes(value)) > INPUT_BYTE_LIMIT:
        raise CompactProtocolError("compact worker input exceeds frozen bound")
    return value


def maximum_native_output(context: Mapping[str, Any], mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Construct the canonical heaviest output permitted for one context."""
    item_map={row["ordinal"]:row for row in mapping["items"]}
    item_ordinals=[row[0] for row in context["i"]]
    action_ordinals=[action[0] for row in context["i"] for action in row[1]]
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
        fid = "frag-" + hashlib.sha256((run_id + "\0" + "\0".join(map(str, group))).encode()).hexdigest()[:16]
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
    plan = {"version": VERSION, "ordinal_map_sha256": digest(mapping),
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
    if not isinstance(native, dict) or set(native) != {"v", "i", "a", "c"} or native.get("v") != 1:
        raise CompactProtocolError("compact native output closure is invalid")
    if len(canonical_bytes(native)) > NATIVE_OUTPUT_BYTE_LIMIT:
        raise CompactProtocolError("compact native output exceeds frozen byte limit")
    expected_items = [row[0] for row in compact["i"]]
    expected_actions = [action[0] for row in compact["i"] for action in row[1]]
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
