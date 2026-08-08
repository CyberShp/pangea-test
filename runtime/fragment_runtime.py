"""Trusted Skill receipts and evidence-bound worker fragment verification."""
from __future__ import annotations

import hashlib
import json
import re
import copy
from pathlib import Path
from typing import Any
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from runtime.context_budget import _validate_trusted_skills, digest, validate as validate_pack
from runtime.obligation_ledger import FINAL

SCHEMA_VERSION = "1.0"
FRAGMENT_VERSION = "2.0"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^[0-9a-f]{128}$")
_TOKENIZED_HOOK_URI = "file://{ISOLATED_EVALUATOR_ROOT}/model-budget-hook/pre-request-budget.js"
_TOKENIZED_HOOK_ARRAY_SHA256 = digest([_TOKENIZED_HOOK_URI])
_EXECUTION_PUBLIC_KEY = Ed25519PublicKey.from_public_bytes(bytes.fromhex(
    "13ffb01419537d380ab8a3743241be4c552c9b6f0d1e3da40f4045f8b1360606"
))
CONTRIBUTION_FAMILIES = ("flows", "branches", "states", "resources", "concurrency",
                         "error_chains", "scenario_candidates", "coverage")


class FragmentError(ValueError):
    pass


def verify_execution_attestation(attestation:dict[str,Any],expected_agent:str) -> tuple[str,dict[str,Any]]:
    """Verify one evaluator-signed leaf-role execution receipt."""
    if (not isinstance(attestation,dict) or set(attestation)!={"artifact_type","schema_version","receipt","signature"}
            or attestation.get("artifact_type")!="role_execution_attestation" or attestation.get("schema_version")!="1.0"
            or not isinstance(attestation.get("receipt"),dict) or not isinstance(attestation.get("signature"),str)
            or not _SIGNATURE.fullmatch(attestation["signature"])):
        raise FragmentError("invalid role execution attestation")
    receipt=attestation["receipt"]
    expected_execution_agents={
        "analysis-worker":{"analysis-worker","analysis-leaf"},
        "auditor":{"auditor","audit-leaf"},
        "mr-reader":{"mr-reader"},
    }.get(expected_agent,set())
    required={"artifact_type","schema_version","captured_by","agent","logical_role","execution_agent","model","opencode_version",
              "cwd_manifest_sha256","artifact_bindings","command_sha256","overlay_sha256",
              "resolved_config_sha256","resolved_permission_rules_sha256","output_payload_sha256",
              "model_call_limit","model_budget_hook_sha256","model_calls_completed",
              "model_requests_admitted","pre_request_budget_blocked","pre_request_budget_enforced",
              "injected_test_runner","evidence_class","plugin_closure","resolved_plugin_closure",
              "session_id","stdout_sha256","exit_code","passed","failures"}
    if (set(receipt)!=required or receipt.get("artifact_type")!="role_execution_receipt"
            or receipt.get("schema_version")!="1.0" or receipt.get("captured_by")!="evaluator"
            or receipt.get("agent")!=expected_agent or receipt.get("logical_role")!=expected_agent
            or receipt.get("execution_agent") not in expected_execution_agents
            or receipt.get("model")!="deepseek/deepseek-v4-flash"
            or receipt.get("opencode_version")!="1.18.4" or receipt.get("exit_code")!=0
            or receipt.get("passed") is not True or receipt.get("failures")!=[]
            or not isinstance(receipt.get("session_id"),str) or not receipt["session_id"]
            or any(not isinstance(receipt.get(field),str) or not _HASH.fullmatch(receipt[field]) for field in
                   ("cwd_manifest_sha256","command_sha256","overlay_sha256","resolved_config_sha256",
                    "resolved_permission_rules_sha256","output_payload_sha256","stdout_sha256",
                    "model_budget_hook_sha256"))
            or not isinstance(receipt.get("artifact_bindings"),list) or not receipt["artifact_bindings"]):
        raise FragmentError("invalid signed role execution receipt")
    model_call_limit=receipt["model_call_limit"]
    model_calls_completed=receipt["model_calls_completed"]
    model_requests_admitted=receipt["model_requests_admitted"]
    injected_test_runner=receipt["injected_test_runner"]
    expected_plugin_closure={
        "plugin_uri":_TOKENIZED_HOOK_URI,
        "plugin_sha256":receipt["model_budget_hook_sha256"],
        "plugin_count":1,
        "plugin_array_sha256":_TOKENIZED_HOOK_ARRAY_SHA256,
    }
    expected_resolved_plugin_closure={
        **expected_plugin_closure,
        "parsed":True,
        "resolved_plugin_count":1,
        "exact":True,
    }
    if (type(model_call_limit) is not int or not 1<=model_call_limit<=40
            or type(model_calls_completed) is not int or not 1<=model_calls_completed<=model_call_limit
            or type(model_requests_admitted) is not int
            or not model_calls_completed<=model_requests_admitted<=model_call_limit
            or receipt["pre_request_budget_blocked"] is not False
            or type(receipt["pre_request_budget_enforced"]) is not bool
            or type(injected_test_runner) is not bool
            or receipt["pre_request_budget_enforced"] is injected_test_runner
            or receipt.get("evidence_class") != ("test-only" if injected_test_runner else "production")
            or receipt["plugin_closure"]!=expected_plugin_closure
            or receipt["resolved_plugin_closure"]!=expected_resolved_plugin_closure):
        raise FragmentError("invalid signed role model budget receipt")
    bindings=receipt["artifact_bindings"]
    if (any(not isinstance(row,dict) or set(row)!={"name","payload_sha256","file_sha256"}
            or not isinstance(row["name"],str) or not row["name"]
            or not isinstance(row["payload_sha256"],str) or not _HASH.fullmatch(row["payload_sha256"])
            or not isinstance(row["file_sha256"],str) or not _HASH.fullmatch(row["file_sha256"])
            for row in bindings) or len({row["name"] for row in bindings})!=len(bindings)):
        raise FragmentError("invalid signed role artifact bindings")
    binding_names={row["name"] for row in bindings}
    compact_alias={"COMPACT_CONTEXT.json":"analysis-leaf","SEMANTIC_BATCH.json":"audit-leaf"}
    expected_alias=next((value for name,value in compact_alias.items() if binding_names=={name}),expected_agent)
    if receipt["execution_agent"]!=expected_alias:
        raise FragmentError("signed role execution alias mismatch")
    if (receipt["execution_agent"] in set(compact_alias.values())
            and (model_call_limit!=1 or model_calls_completed!=1 or model_requests_admitted!=1
                 or receipt["pre_request_budget_blocked"] is not False)):
        raise FragmentError("invalid signed compact role model budget receipt")
    serialized=json.dumps(receipt,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    try: _EXECUTION_PUBLIC_KEY.verify(bytes.fromhex(attestation["signature"]),serialized)
    except (InvalidSignature,ValueError) as exc: raise FragmentError("role execution signature mismatch") from exc
    return digest(receipt),receipt


def _receipt_payload(skill_id: str, version: str, content_hash: str, triggers: list[str],
                     obligations: list[str], na_boundary: str) -> dict[str, Any]:
    return {
        "artifact_type": "skill_receipt", "schema_version": SCHEMA_VERSION,
        "skill_id": skill_id, "version": version, "content_sha256": content_hash,
        "trigger_inventory_ids": sorted(triggers), "obligation_ids": sorted(obligations),
        "na_boundary": na_boundary,
    }


def _id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return "SR-" + hashlib.sha256(encoded).hexdigest()[:16]

def _canonical_id(prefix: str, payload: dict[str, Any]) -> str:
    return prefix + hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()[:16]

def upgrade_legacy(fragment:dict[str,Any]) -> dict[str,Any]:
    """Upgrade repository-local v1 fixtures; R2 analysis-worker cannot use v1."""
    if fragment.get("schema_version")!="1.0": return copy.deepcopy(fragment)
    if fragment.get("worker_instance")=="analysis-worker": raise FragmentError("analysis-worker requires fragment v2")
    out=copy.deepcopy(fragment); out["schema_version"]=FRAGMENT_VERSION
    def convert_key(value:Any) -> Any:
        if isinstance(value,list) and len(value)==4 and type(value[2]) is int and type(value[3]) is int:
            return [value[0],value[1],value[2],value[3]-value[2]+1]
        return value
    for fact in out.get("facts",[]):
        if isinstance(fact,dict) and "line_end" in fact and "line_count" not in fact:
            fact["line_count"]=fact.pop("line_end")-fact["line_start"]+1
    for disposition in out.get("dispositions",[]):
        if isinstance(disposition,dict) and "counterevidence_fact_keys" in disposition:
            disposition["counterevidence_fact_keys"]=[convert_key(key) for key in disposition["counterevidence_fact_keys"]]
    legacy=out.get("contributions",{}).get("flows",[]); contributions={k:[] for k in CONTRIBUTION_FAMILIES}
    for flow in legacy:
        payload={"priority":flow["priority"],"obligation_id":flow["obligation_id"],"fact_keys":[convert_key(flow["fact_key"])],
                 "summary":"legacy flow contribution","controls":flow["controls"],"oracles":flow["oracles"]}
        contributions["flows"].append({"contribution_id":_canonical_id("C-",payload),**payload})
    out["contributions"]=contributions; converted=[]
    for risk in out.get("risk_cards",[]):
        if risk.get("severity") in {"High","Critical"}: raise FragmentError("legacy H/C risk lacks causal chain")
        payload={"severity":risk["severity"],"obligation_id":risk["obligation_id"],"fact_keys":[convert_key(risk["fact_key"])],"summary":risk["summary"]}
        converted.append({"risk_id":_canonical_id("R-",payload),**payload})
    out["risk_cards"]=converted
    return out

def validate_runner_telemetry(telemetry: dict[str,Any], fragment: dict[str,Any], candidate_sha256: str) -> None:
    """Validate runner-owned telemetry; worker-reported ``usage`` is never a gate."""
    required={"artifact_type","schema_version","run_id","fragment_id","model","candidate_sha256","fragment_sha256",
              "context_sha256","session_id","execution_receipt_sha256",
              "input_tokens","output_tokens","finish_reason","valid_json","captured_by"}
    if (not isinstance(telemetry,dict) or set(telemetry)!=required or telemetry.get("artifact_type")!="runner_telemetry"
            or telemetry.get("schema_version")!="1.0" or telemetry.get("run_id")!=fragment.get("run_id")
            or telemetry.get("fragment_id")!=fragment.get("fragment_id") or telemetry.get("model")!="deepseek/deepseek-v4-flash"
            or telemetry.get("candidate_sha256")!=candidate_sha256 or telemetry.get("fragment_sha256")!=digest(fragment)
            or not isinstance(telemetry.get("context_sha256"),str) or not _HASH.fullmatch(telemetry["context_sha256"])
            or not isinstance(telemetry.get("session_id"),str) or not telemetry["session_id"]
            or not isinstance(telemetry.get("execution_receipt_sha256"),str) or not _HASH.fullmatch(telemetry["execution_receipt_sha256"])
            or type(telemetry.get("input_tokens")) is not int or not 1<=telemetry["input_tokens"]<=180000
            or type(telemetry.get("output_tokens")) is not int or not 1<=telemetry["output_tokens"]<=4096
            or telemetry.get("finish_reason")!="stop" or telemetry.get("valid_json") is not True
            or telemetry.get("captured_by")!="opencode-runner"):
        raise FragmentError("invalid runner telemetry")


def skill_receipt(skill_id: str, trigger_ids: list[str], obligation_ids: list[str],
                  trusted_skills: dict[str, dict[str, str]], na_boundary: str) -> dict[str, Any]:
    try:
        _validate_trusted_skills(trusted_skills)
    except ValueError as exc:
        raise FragmentError(str(exc)) from exc
    if skill_id not in trusted_skills:
        raise FragmentError("untrusted skill")
    if (not isinstance(trigger_ids, list) or not trigger_ids
            or not isinstance(obligation_ids, list) or not obligation_ids
            or any(not isinstance(value, str) for value in trigger_ids + obligation_ids)
            or len(trigger_ids) != len(set(trigger_ids)) or len(obligation_ids) != len(set(obligation_ids))
            or not isinstance(na_boundary, str) or not na_boundary.strip()):
        raise FragmentError("bad receipt references")
    source = trusted_skills[skill_id]
    content_hash = hashlib.sha256(source["content"].encode()).hexdigest()
    payload = _receipt_payload(skill_id, source["version"], content_hash,
                               trigger_ids, obligation_ids, na_boundary)
    return {**payload, "receipt_id": _id(payload)}


def validate_receipt(receipt: dict[str, Any], inventory: dict[str, Any], ledger: dict[str, Any],
                     trusted_skills: dict[str, dict[str, str]]) -> None:
    try:
        _validate_trusted_skills(trusted_skills)
    except ValueError as exc:
        raise FragmentError(str(exc)) from exc
    required = {"artifact_type", "schema_version", "receipt_id", "skill_id", "version", "content_sha256",
                "trigger_inventory_ids", "obligation_ids", "na_boundary"}
    if (not isinstance(receipt, dict) or set(receipt) != required
            or receipt.get("artifact_type") != "skill_receipt"
            or receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("skill_id") not in trusted_skills):
        raise FragmentError("invalid receipt")
    trigger_ids, obligation_ids = receipt["trigger_inventory_ids"], receipt["obligation_ids"]
    if (not isinstance(trigger_ids, list) or not trigger_ids
            or not isinstance(obligation_ids, list) or not obligation_ids
            or not isinstance(receipt["na_boundary"], str) or not receipt["na_boundary"].strip()
            or any(not isinstance(value, str) for value in trigger_ids + obligation_ids)
            or len(trigger_ids) != len(set(trigger_ids)) or len(obligation_ids) != len(set(obligation_ids))):
        raise FragmentError("bad receipt references")
    source = trusted_skills[receipt["skill_id"]]
    content_hash = hashlib.sha256(source["content"].encode()).hexdigest()
    payload = _receipt_payload(receipt["skill_id"], receipt["version"], content_hash,
                               trigger_ids, obligation_ids, receipt["na_boundary"])
    if (not isinstance(receipt["version"], str) or receipt["version"] != source["version"]
            or not isinstance(receipt["content_sha256"], str) or receipt["content_sha256"] != content_hash
            or not _HASH.fullmatch(receipt["content_sha256"])
            or not isinstance(receipt["receipt_id"], str) or receipt["receipt_id"] != _id(payload)):
        raise FragmentError("forged receipt")
    items = {item["inventory_id"]: item for item in inventory["items"]}
    rows = {row["obligation_id"]: row for row in ledger["obligations"]}
    if not set(trigger_ids) <= set(items) or not set(obligation_ids) <= set(rows):
        raise FragmentError("bad receipt references")
    obligation_triggers: set[str] = set()
    for oid in obligation_ids:
        item = items[rows[oid]["inventory_id"]]
        obligation_triggers.add(item["inventory_id"])
        if receipt["skill_id"] not in item["storage_skill_triggers"]:
            raise FragmentError("receipt trigger does not match obligation")
    if obligation_triggers != set(trigger_ids):
        raise FragmentError("receipt trigger and obligation coverage differ")


def _fact_key(value: Any) -> tuple[Any, ...] | None:
    if (not isinstance(value, list) or len(value) != 4 or not isinstance(value[0], str)
            or not isinstance(value[1], str) or type(value[2]) is not int or type(value[3]) is not int
            or value[2] < 1 or value[3] < 1):
        return None
    return tuple(value)


def _covered_graph(fragment_dispositions: list[dict[str, Any]], ledger: dict[str, Any]) -> None:
    nodes: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for row in ledger["obligations"]:
        if row["status"] == "complete" and isinstance(row["disposition"], dict):
            nodes[row["obligation_id"]] = (row["disposition"], row)
    ledger_rows = {row["obligation_id"]: row for row in ledger["obligations"]}
    nodes.update({disposition["obligation_id"]: (disposition, ledger_rows[disposition["obligation_id"]])
                  for disposition in fragment_dispositions})
    for disposition in fragment_dispositions:
        if disposition["outcome"] != "covered_by_other":
            continue
        origin = ledger_rows[disposition["obligation_id"]]
        if origin["action"] == "cover_source":
            raise FragmentError("source chunks cannot be covered_by_other")
        current_id = disposition["obligation_id"]
        seen: set[str] = set()
        while True:
            if current_id in seen:
                raise FragmentError("covered_by cycle")
            seen.add(current_id)
            node = nodes.get(current_id)
            if not node:
                raise FragmentError("covered_by target missing")
            current, current_row = node
            if current_row["inventory_id"] != origin["inventory_id"]:
                raise FragmentError("covered_by must stay within one inventory item")
            if current_row.get("action") != origin.get("action"):
                raise FragmentError("covered_by may not erase a distinct action dimension")
            outcome = current.get("outcome")
            if outcome == "analyzed":
                break
            if outcome != "covered_by_other":
                raise FragmentError("covered_by chain must terminate at analyzed")
            current_id = current.get("covered_by")
            if not isinstance(current_id, str):
                raise FragmentError("bad covered_by")


def validate(fragment: dict[str, Any], pack: dict[str, Any], inventory: dict[str, Any],
             ledger: dict[str, Any], trusted_root: str, receipts: list[dict[str, Any]],
             trusted_skills: dict[str, dict[str, str]]) -> None:
    fragment=upgrade_legacy(fragment)
    validate_pack(pack, inventory, ledger, trusted_root, receipts, trusted_skills)
    required = {"artifact_type", "schema_version", "worker_instance", "run_id", "fragment_id", "context_pack_sha256",
                "obligation_ids", "skill_receipt_ids", "facts", "contributions", "risk_cards", "dispositions",
                "unresolved", "usage"}
    if (not isinstance(fragment, dict) or set(fragment) != required
            or fragment.get("artifact_type") != "analysis_fragment"
            or fragment.get("schema_version") != FRAGMENT_VERSION
            or any(not isinstance(fragment.get(key), str) or not fragment[key].strip()
                   for key in ("worker_instance", "run_id", "fragment_id"))
            or fragment["run_id"] != pack["run_id"] or fragment["fragment_id"] != pack["fragment_id"]
            or fragment.get("context_pack_sha256") != digest(pack)):
        raise FragmentError("fragment binding")
    # Retained only as a non-authoritative worker diagnostic.  Acceptance and
    # Judge gates consume validate_runner_telemetry() instead.
    usage = fragment.get("usage")
    if (not isinstance(usage, dict) or set(usage) != {"output_tokens", "finish_reason", "valid_json"}
            or type(usage["output_tokens"]) is not int or not 1 <= usage["output_tokens"] <= 4096
            or usage["valid_json"] is not True or usage["finish_reason"] not in {"stop", "tool"}):
        raise FragmentError("invalid/truncated usage")
    wanted = pack["obligation_ids"]
    if (fragment.get("obligation_ids") != wanted or not isinstance(wanted, list)
            or len(wanted) != len(set(wanted))):
        raise FragmentError("obligation mismatch")
    dispositions = fragment.get("dispositions")
    if (not isinstance(dispositions, list) or len(dispositions) != len(wanted)
            or any(not isinstance(value, dict) or not isinstance(value.get("obligation_id"), str) for value in dispositions)
            or len({value["obligation_id"] for value in dispositions}) != len(wanted)
            or {value["obligation_id"] for value in dispositions} != set(wanted)):
        raise FragmentError("missing or duplicate disposition")
    if not isinstance(receipts, list):
        raise FragmentError("invalid receipt collection")
    for receipt in receipts:
        validate_receipt(receipt, inventory, ledger, trusted_skills)
    by_receipt = {receipt["receipt_id"]: receipt for receipt in receipts}
    if len(by_receipt) != len(receipts):
        raise FragmentError("duplicate receipt artifacts")
    pack_receipt_ids = [ref["receipt_id"] for ref in pack["skill_receipts"]]
    if (not isinstance(fragment.get("skill_receipt_ids"), list)
            or fragment["skill_receipt_ids"] != pack_receipt_ids
            or len(fragment["skill_receipt_ids"]) != len(set(fragment["skill_receipt_ids"]))):
        raise FragmentError("fragment receipts differ from injected receipts")
    selected_receipts: list[dict[str, Any]] = []
    for receipt_id in fragment["skill_receipt_ids"]:
        if receipt_id not in by_receipt:
            raise FragmentError("missing receipt")
        validate_receipt(by_receipt[receipt_id], inventory, ledger, trusted_skills)
        selected_receipts.append(by_receipt[receipt_id])
    rows = {row["obligation_id"]: row for row in ledger["obligations"]}
    items = {item["inventory_id"]: item for item in inventory["items"]}
    for oid in wanted:
        required_skills = set(items[rows[oid]["inventory_id"]]["storage_skill_triggers"])
        got = {receipt["skill_id"] for receipt in selected_receipts if oid in receipt["obligation_ids"]}
        if got != required_skills:
            raise FragmentError("applicable skill receipt set mismatch")
    facts = fragment.get("facts")
    if not isinstance(facts, list):
        raise FragmentError("bad facts")
    root = Path(trusted_root).resolve()
    seen_facts: set[tuple[Any, ...]] = set()
    for fact in facts:
        keys = {"obligation_id", "inventory_id", "path", "line_start", "line_count", "excerpt_sha256", "evidence"}
        if (not isinstance(fact, dict) or set(fact) != keys or fact["obligation_id"] not in wanted
                or fact["inventory_id"] != rows[fact["obligation_id"]]["inventory_id"]
                or type(fact["line_start"]) is not int or type(fact["line_count"]) is not int
                or fact["line_start"] < 1 or fact["line_count"] < 1
                or not isinstance(fact["excerpt_sha256"], str) or not _HASH.fullmatch(fact["excerpt_sha256"])
                or not isinstance(fact["evidence"], str) or fact["evidence"]!=fact["evidence"].strip()
                or len(fact["evidence"].encode())<8):
            raise FragmentError("fact outside obligation binding")
        key = (fact["obligation_id"], fact["inventory_id"], fact["line_start"], fact["line_count"])
        if key in seen_facts:
            raise FragmentError("duplicate fact")
        seen_facts.add(key)
        windows = [window for window in pack["allowed_ranges"] if fact["inventory_id"] in window["inventory_ids"]]
        if len(windows) != 1:
            raise FragmentError("fact inventory lacks unique range")
        window = windows[0]
        line_end=fact["line_start"]+fact["line_count"]-1
        if (fact["path"] != window["path"]
                or not window["line_start"] <= fact["line_start"] <= line_end <= window["line_end"]):
            raise FragmentError("fact range outside pack")
        lines = (root / fact["path"]).read_text(errors="replace").splitlines() or [""]
        expected = hashlib.sha256("\n".join(lines[fact["line_start"] - 1:line_end]).encode()).hexdigest()
        if expected != fact["excerpt_sha256"]:
            raise FragmentError("unverifiable fact")
    dispositions_by_id = {value["obligation_id"]: value for value in dispositions}
    for disposition in dispositions:
        if (disposition.get("outcome") not in FINAL or not isinstance(disposition.get("reason"), str)
                or disposition["reason"]!=disposition["reason"].strip()
                or len(disposition["reason"].encode())<12):
            raise FragmentError("weak disposition")
        expected = {"obligation_id", "outcome", "reason"}
        if disposition["outcome"] == "covered_by_other":
            expected.add("covered_by")
        if disposition["outcome"] == "not_applicable":
            expected |= {"boundary", "counterevidence_fact_keys"}
        if set(disposition) != expected:
            raise FragmentError("outcome fields invalid")
        oid = disposition["obligation_id"]
        own_facts = {key for key in seen_facts if key[0] == oid}
        if disposition["outcome"] == "analyzed" and not own_facts:
            raise FragmentError("analyzed lacks evidence")
        if disposition["outcome"] == "not_applicable":
            counter = disposition["counterevidence_fact_keys"]
            if (not isinstance(disposition["boundary"], str) or not disposition["boundary"].strip()
                    or not isinstance(counter, list) or not counter
                    or any(_fact_key(key) not in own_facts for key in counter)
                    or len(counter) != len({tuple(key) for key in counter})):
                raise FragmentError("N/A lacks exact counterevidence")
        item = items[rows[oid]["inventory_id"]]
        if disposition["outcome"] in {"analyzed", "not_applicable"}:
            core_facts = (own_facts if disposition["outcome"] == "analyzed"
                          else {_fact_key(key) for key in disposition["counterevidence_fact_keys"]})
            if item["kind"] == "source_chunk" and not any(
                    key is not None and key[0] == oid and key[2] == item["line_start"]
                    and key[3] == item["line_end"]-item["line_start"]+1 for key in core_facts):
                raise FragmentError("source chunk disposition requires whole-chunk evidence")
            if not any(key is not None and key[0] == oid
                       and max(key[2], item["line_start"]) <= min(key[2]+key[3]-1, item["line_end"])
                       for key in core_facts):
                raise FragmentError("disposition evidence does not intersect inventory item")
    _covered_graph(dispositions, ledger)
    risks = fragment.get("risk_cards")
    if not isinstance(risks, list):
        raise FragmentError("bad risks")
    seen_risks:set[str]=set()
    for card in risks:
        base={"risk_id","severity","obligation_id","fact_keys","summary"}; severity=card.get("severity") if isinstance(card,dict) else None
        expected=base|({"trigger","propagation","impact","observation","recovery","control","oracle"} if severity in {"High","Critical"} else set())
        fact_keys=card.get("fact_keys") if isinstance(card,dict) else None
        payload={k:card[k] for k in sorted(set(card)-{"risk_id"})} if isinstance(card,dict) else {}
        if (not isinstance(card, dict) or set(card) != expected or severity not in {"Low","Medium","High","Critical"}
                or card.get("obligation_id") not in wanted or not isinstance(fact_keys,list) or not fact_keys
                or any(_fact_key(key) not in seen_facts or key[0]!=card["obligation_id"] for key in fact_keys)
                or len({tuple(key) for key in fact_keys})!=len(fact_keys)
                or not isinstance(card.get("summary"),str) or not card["summary"].strip()
                or card.get("risk_id")!=_canonical_id("R-",payload) or card["risk_id"] in seen_risks
                or any(not isinstance(card.get(k),str) or not card[k].strip() for k in expected-base)):
            raise FragmentError("risk lacks exact evidence")
        seen_risks.add(card["risk_id"])
    contributions = fragment.get("contributions")
    if not isinstance(contributions,dict) or set(contributions)!=set(CONTRIBUTION_FAMILIES):
        raise FragmentError("bad contributions")
    seen_contributions:set[str]=set()
    for family in CONTRIBUTION_FAMILIES:
        values=contributions[family]
        if not isinstance(values,list): raise FragmentError("bad contributions")
        for value in values:
            required={"contribution_id","priority","obligation_id","fact_keys","summary","controls","oracles"}
            payload={k:value[k] for k in sorted(set(value)-{"contribution_id"})} if isinstance(value,dict) else {}
            keys=value.get("fact_keys") if isinstance(value,dict) else None
            if (not isinstance(value,dict) or set(value)!=required or value.get("obligation_id") not in wanted
                    or value.get("priority") not in {"P0","P1","P2","P3"} or not isinstance(keys,list) or not keys
                    or any(_fact_key(key) not in seen_facts or key[0]!=value["obligation_id"] for key in keys)
                    or not isinstance(value.get("summary"),str) or not value["summary"].strip()
                    or any(not isinstance(value.get(k),list) or len(value[k])!=len(set(value[k]))
                           or any(not isinstance(x,str) or not x.strip() for x in value[k]) for k in ("controls","oracles"))
                    or value.get("contribution_id")!=_canonical_id("C-",payload) or value["contribution_id"] in seen_contributions):
                raise FragmentError("bad contribution")
            if value["priority"] in {"P0","P1"} and (not value["controls"] or not value["oracles"]):
                raise FragmentError("P0/P1 lacks verification")
            seen_contributions.add(value["contribution_id"])
    unresolved = fragment.get("unresolved")
    if not isinstance(unresolved, list):
        raise FragmentError("bad unresolved")
    unresolved_ids: list[str] = []
    for item in unresolved:
        if (not isinstance(item, dict) or set(item) != {"obligation_id", "reason", "next_step"}
                or item["obligation_id"] not in wanted or item["obligation_id"] in unresolved_ids
                or any(not isinstance(item[key], str) or not item[key].strip() for key in ("reason", "next_step"))
                or dispositions_by_id[item["obligation_id"]]["outcome"] not in {"blocked", "need_verify"}):
            raise FragmentError("bad unresolved")
        unresolved_ids.append(item["obligation_id"])
    for disposition in dispositions:
        incomplete = disposition["outcome"] in {"blocked", "need_verify"}
        if incomplete != (disposition["obligation_id"] in unresolved_ids):
            raise FragmentError("unresolved/disposition mismatch")


def validate_and_apply(ledger: dict[str, Any], fragment: dict[str, Any], pack: dict[str, Any],
                       inventory: dict[str, Any], trusted_root: str, receipts: list[dict[str, Any]],
                       trusted_skills: dict[str, dict[str, str]]) -> dict[str, Any]:
    fragment=upgrade_legacy(fragment)
    validate(fragment, pack, inventory, ledger, trusted_root, receipts, trusted_skills)
    by_receipt = {receipt["receipt_id"]: receipt for receipt in receipts}
    receipt_map = {oid: [ref["receipt_id"] for ref in pack["skill_receipts"]
                         if oid in by_receipt[ref["receipt_id"]]["obligation_ids"]]
                   for oid in fragment["obligation_ids"]}
    from runtime.obligation_ledger import _apply_validated
    return _apply_validated(ledger, fragment, inventory, trusted_root, receipt_map)

def merge_fragments(fragments:list[dict[str,Any]]) -> dict[str,Any]:
    """Deterministically merge validated fragments without resolving conflicts."""
    if not isinstance(fragments,list) or not fragments: raise FragmentError("missing fragments")
    run_ids={x.get("run_id") for x in fragments}; ids=[x.get("fragment_id") for x in fragments]
    if len(run_ids)!=1 or None in run_ids or len(ids)!=len(set(ids)): raise FragmentError("fragment merge binding conflict")
    merged={family:[] for family in CONTRIBUTION_FAMILIES}; risks=[]; facts=[]; dispositions=[]
    registries:dict[str,dict[str,Any]]={"facts":{},"risks":{},"dispositions":{}}
    for fragment in sorted(fragments,key=lambda x:x["fragment_id"]):
        for fact in fragment["facts"]:
            key=json.dumps([fact[k] for k in ("obligation_id","inventory_id","line_start","line_count")])
            prior=registries["facts"].get(key)
            if prior is not None and prior!=fact: raise FragmentError("fact merge conflict")
            if prior is None: registries["facts"][key]=fact; facts.append(fact)
        for family in CONTRIBUTION_FAMILIES:
            known={x["contribution_id"]:x for x in merged[family]}
            for item in fragment["contributions"][family]:
                if item["contribution_id"] in known and known[item["contribution_id"]]!=item: raise FragmentError("contribution merge conflict")
                if item["contribution_id"] not in known: merged[family].append(item)
        for risk in fragment["risk_cards"]:
            prior=registries["risks"].get(risk["risk_id"])
            if prior is not None and prior!=risk: raise FragmentError("risk merge conflict")
            if prior is None: registries["risks"][risk["risk_id"]]=risk; risks.append(risk)
        for disposition in fragment["dispositions"]:
            oid=disposition["obligation_id"]; prior=registries["dispositions"].get(oid)
            if prior is not None and prior!=disposition: raise FragmentError("disposition merge conflict")
            if prior is None: registries["dispositions"][oid]=disposition; dispositions.append(disposition)
    for family in merged: merged[family].sort(key=lambda x:x["contribution_id"])
    body={"artifact_type":"merged_analysis","schema_version":"1.0","run_id":next(iter(run_ids)),
            "fragment_ids":sorted(ids),"facts":sorted(facts,key=lambda x:(x["obligation_id"],x["inventory_id"],x["line_start"],x["line_count"])),
            "contributions":merged,"risk_cards":sorted(risks,key=lambda x:x["risk_id"]),
            "dispositions":sorted(dispositions,key=lambda x:x["obligation_id"])}
    return {**body,"sha256":digest(body)}
