"""Independent deterministic coverage judge for complete module analysis."""
from __future__ import annotations

from typing import Any

from runtime import analysis_reporting
from runtime import fragment_runtime

CHECKS = (
    "model_integrity", "breadth_disposition", "scenario_derivation",
    "test_traceability", "report_projection",
)


def _ids(items: list[dict[str, Any]], field: str) -> set[str]:
    return {str(item[field]) for item in items if isinstance(item, dict) and item.get(field)}


def judge(analysis: dict[str, Any], report: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    findings: dict[str, list[str]] = {name: [] for name in CHECKS}
    entrypoints = _ids(analysis["entrypoints"], "entrypoint_id")
    flows = _ids(analysis["flows"], "flow_id")
    branches = _ids(analysis["branches"], "branch_id")
    states = _ids(analysis["states"], "state_id")
    resources = _ids(analysis["resources"], "resource_id")
    concurrency = _ids(analysis["concurrency"], "concurrency_id")
    errors = _ids(analysis["error_chains"], "chain_id")
    candidates = _ids(analysis["scenario_candidates"], "candidate_id")
    sfmea = _ids(analysis["sfmea"], "sfmea_id")
    scenarios = _ids(analysis["test_scenarios"], "scenario_id")
    test_flows = _ids(analysis["test_flows"], "test_flow_id")
    cases = _ids(analysis["test_cases"], "case_id")
    trace = _ids(analysis["traceability"], "trace_id")
    all_ids = entrypoints | flows | branches | states | resources | concurrency | errors | candidates | sfmea | scenarios | test_flows | cases | trace

    for item in analysis["entrypoints"]:
        unknown = set(map(str, item["flow_ids"])) - flows
        if unknown:
            findings["model_integrity"].append(f"入口 {item['entrypoint_id']} 引用未知 Flow: {sorted(unknown)}")
    for item in analysis["flows"]:
        if item["entrypoint_id"] not in entrypoints:
            findings["model_integrity"].append(f"Flow {item['flow_id']} 引用未知入口 {item['entrypoint_id']}")
    for item in analysis["traceability"]:
        unknown = (set(map(str, item["source_ids"])) | set(map(str, item["target_ids"]))) - all_ids
        if unknown:
            findings["model_integrity"].append(f"追溯 {item['trace_id']} 引用未知 ID: {sorted(unknown)}")

    dispositions = {str(item["item_id"]): item for item in analysis["coverage_dispositions"]}
    mandatory = entrypoints | flows | branches | states | resources | concurrency | errors | candidates
    for item_id in sorted(mandatory - set(dispositions)):
        findings["breadth_disposition"].append(f"缺少 Coverage disposition: {item_id}")
    cover_targets = scenarios | test_flows | cases
    for item_id, item in dispositions.items():
        covered_by = set(map(str, item.get("covered_by", [])))
        unknown = covered_by - cover_targets
        if unknown:
            findings["breadth_disposition"].append(f"{item_id} covered_by 引用未知测试工件: {sorted(unknown)}")
        if item["outcome"] in {"analyzed", "covered_by_other"} and not covered_by:
            findings["breadth_disposition"].append(f"{item_id} 已分析但未绑定场景、测试流程或用例")

    candidate_by_id = {str(item["candidate_id"]): item for item in analysis["scenario_candidates"]}
    scenario_candidate_refs = {str(ref) for item in analysis["test_scenarios"] for ref in item["source_candidate_ids"]}
    for candidate_id, item in candidate_by_id.items():
        targets = set(map(str, item["target_ids"]))
        unknown = targets - (scenarios | cases)
        if unknown:
            findings["scenario_derivation"].append(f"候选 {candidate_id} target_ids 未落到场景或用例: {sorted(unknown)}")
        if item["disposition"] == "retained" and candidate_id not in scenario_candidate_refs:
            findings["scenario_derivation"].append(f"保留候选 {candidate_id} 未被测试场景消费")
    for item in analysis["sfmea"]:
        unknown_scenarios = set(map(str, item["scenario_ids"])) - scenarios
        unknown_cases = set(map(str, item["test_case_ids"])) - cases
        if unknown_scenarios or unknown_cases:
            findings["scenario_derivation"].append(
                f"SFMEA {item['sfmea_id']} 引用未知场景/用例: {sorted(unknown_scenarios | unknown_cases)}"
            )

    flow_scenarios = {str(item["scenario_id"]) for item in analysis["test_flows"]}
    flow_cases = {str(case_id) for item in analysis["test_flows"] for case_id in item["test_case_ids"]}
    for scenario_id in sorted(scenarios - flow_scenarios):
        findings["test_traceability"].append(f"场景 {scenario_id} 没有黑盒测试流程")
    for case_id in sorted(cases - flow_cases):
        findings["test_traceability"].append(f"用例 {case_id} 没有被测试流程编排")
    risk_by_id = {str(item["risk_id"]): item for item in ledger.get("risks", []) if isinstance(item, dict)}
    executable_refs = {str(risk_id) for item in analysis["test_scenarios"] for risk_id in item["risk_ids"]}
    executable_refs |= {str(risk_id) for item in analysis["test_cases"] for risk_id in item["risk_ids"]}
    for risk_id, risk in risk_by_id.items():
        if risk.get("translation_status") != "Developer-confirm" and risk_id not in executable_refs:
            findings["test_traceability"].append(f"可执行风险 {risk_id} 未映射到场景或用例")
    for risk_id in sorted(executable_refs - set(risk_by_id)):
        findings["test_traceability"].append(f"测试工件引用风险账本外风险: {risk_id}")
    for item in analysis["test_flows"]:
        if not item.get("oracles"):
            findings["test_traceability"].append(f"测试流程 {item['test_flow_id']} 缺少独立 Oracle")
    for item in analysis["scenario_candidates"]:
        if not str(item.get("oracle", "")).strip():
            findings["test_traceability"].append(f"场景候选 {item['candidate_id']} 缺少独立 Oracle")

    unresolved_ids = {str(item.get("item_id")) for item in analysis.get("unresolved", []) if isinstance(item, dict)}
    for item in analysis["evidence_consumption"]:
        if item["status"] in {"blocked", "unreadable", "partially_parsed"} and item["evidence_id"] not in unresolved_ids:
            findings["model_integrity"].append(f"材料 {item['evidence_id']} 未完整消费但未进入 unresolved")

    try:
        analysis_reporting.assert_projection(report, analysis)
    except ValueError as exc:
        findings["report_projection"].append(str(exc))

    checks = {
        name: {"verdict": "PASS" if not items else "FAIL", "findings": items}
        for name, items in findings.items()
    }
    verdict = "PASS" if all(check["verdict"] == "PASS" for check in checks.values()) else "FAIL"
    return {"verdict": verdict, "checks": checks}

R2_GATES={"evidence_refs":100.0,"action_quality":100.0,"semantic_support":97.0,"p0":95.0,"p1":90.0,"blackbox":90.0,
          "na_specificity":95.0,"applicable_disposition":100.0,"hc_retention":100.0,"telemetry":100.0}

def _rate(ok:int,total:int) -> float:
    return 100.0 if total==0 else round(ok*100.0/total,2)

def _unique_map(values:list[dict[str,Any]], key:str, label:str) -> dict[str,dict[str,Any]]:
    if any(not isinstance(value,dict) or not isinstance(value.get(key),str) or not value[key] for value in values):
        raise ValueError("invalid R2 artifact identity: "+label)
    out={value[key]:value for value in values}
    if len(out)!=len(values): raise ValueError("duplicate R2 artifact identity: "+label)
    return out

def _publication_id(value:dict[str,Any]) -> str:
    if not isinstance(value,dict) or value.get("status")!="committed": raise ValueError("uncommitted R2 publication")
    if "artifacts" in value and "contexts" not in value: return "denominator"
    if "contexts" in value and "assignments" in value and "artifacts" not in value: return "context"
    raise ValueError("unknown R2 publication manifest")

def _expected_artifact_bindings(inputs:dict[str,Any]) -> list[dict[str,str]]:
    specs=(("inventory","repository","inventories"),("ledger","repository","ledgers"),
           ("assignment","fragment_id","assignments"),("fragment","fragment_id","fragments"),
           ("compact_native_output","fragment_id","native_outputs"),("compact_adapter_receipt","fragment_id","adapter_receipts"),
           ("skill_receipt","receipt_id","skill_receipts"),("runner_telemetry","fragment_id","telemetry"),
           ("semantic_assessment","claim_id","semantic_assessments"))
    rows=[]
    for artifact_type,key,collection in specs:
        for value in inputs[collection]:
            rows.append({"artifact_type":artifact_type,"artifact_id":value[key],"payload_sha256":fragment_runtime.digest(value)})
    for value in inputs["publication_manifests"]:
        rows.append({"artifact_type":"publication_manifest","artifact_id":_publication_id(value),"payload_sha256":fragment_runtime.digest(value)})
    for value in inputs["execution_attestations"]:
        receipt_hash=fragment_runtime.digest(value.get("receipt")) if isinstance(value,dict) else ""
        rows.append({"artifact_type":"execution_attestation","artifact_id":receipt_hash,"payload_sha256":fragment_runtime.digest(value)})
    return sorted(rows,key=lambda x:(x["artifact_type"],x["artifact_id"]))

def judge_r2(inputs:dict[str,Any]) -> dict[str,Any]:
    """Judge only closed, producer-independent R2 values and runner receipts.

    Filesystem bindings are added by the trusted run controller after this pure
    value judge returns; accepting them here would let an untrusted caller make
    unverifiable claims about paths and hashes.
    """
    required={"run_id","inventories","ledgers","assignments","fragments","native_outputs","adapter_receipts","skill_receipts","telemetry","semantic_assessments","publication_manifests","execution_attestations","artifact_bindings"}
    if not isinstance(inputs,dict) or set(inputs)!=required: raise ValueError("invalid R2 Judge input contract")
    run_id=inputs["run_id"]
    for key in required-{"run_id"}:
        if not isinstance(inputs[key],list): raise ValueError("invalid R2 Judge collection: "+key)
    publications=_unique_map([{"publication_id":_publication_id(x),**x} for x in inputs["publication_manifests"]],"publication_id","publication")
    if set(publications)!={"denominator","context"}: raise ValueError("R2 publication manifest set mismatch")
    inventories=_unique_map(inputs["inventories"],"repository","inventory repository")
    ledgers=_unique_map(inputs["ledgers"],"repository","ledger repository")
    if set(inventories)!=set(ledgers): raise ValueError("R2 denominator repository mismatch")
    if any(not inventory.get("items") for inventory in inventories.values()):
        raise ValueError("R2 inventory repository must not be empty")
    if any(not ledger.get("obligations") for ledger in ledgers.values()):
        raise ValueError("R2 ledger repository must not be empty")
    inventory_items=[item for inv in inventories.values() for item in inv.get("items",[])]
    inventory_by_id=_unique_map(inventory_items,"inventory_id","inventory item")
    obligation_rows=[(repo,row) for repo,ledger in ledgers.items() for row in ledger.get("obligations",[])]
    obligations=_unique_map([row for _,row in obligation_rows],"obligation_id","obligation")
    if any(row.get("inventory_id") not in inventory_by_id for row in obligations.values()): raise ValueError("obligation inventory set mismatch")
    inventory_repo={item["inventory_id"]:repo for repo,inventory in inventories.items() for item in inventory.get("items",[])}
    if any(inventory_repo[row["inventory_id"]]!=repo for repo,row in obligation_rows):
        raise ValueError("obligation inventory repository mismatch")
    if {row["inventory_id"] for row in obligations.values()}!=set(inventory_by_id):
        raise ValueError("inventory denominator contains unused item")
    assignments=_unique_map(inputs["assignments"],"fragment_id","assignment")
    fragment_map=_unique_map(inputs["fragments"],"fragment_id","fragment")
    receipts=_unique_map(inputs["skill_receipts"],"receipt_id","skill receipt")
    telemetry_by_id=_unique_map(inputs["telemetry"],"fragment_id","telemetry")
    assessment_map=_unique_map(inputs["semantic_assessments"],"claim_id","semantic assessment")
    attestation_map:dict[str,tuple[dict[str,Any],dict[str,Any]]]={}
    for attestation in inputs["execution_attestations"]:
        agent=attestation.get("receipt",{}).get("agent") if isinstance(attestation,dict) else None
        if agent not in {"analysis-worker","auditor"}: raise ValueError("invalid execution attestation agent")
        try: receipt_hash,receipt=fragment_runtime.verify_execution_attestation(attestation,agent)
        except fragment_runtime.FragmentError as exc: raise ValueError(str(exc)) from exc
        if receipt_hash in attestation_map: raise ValueError("duplicate execution attestation")
        attestation_map[receipt_hash]=(attestation,receipt)
    if set(assignments)!=set(fragment_map) or set(telemetry_by_id)!=set(fragment_map): raise ValueError("assignment/fragment/telemetry set mismatch")
    fragments=sorted(fragment_map.values(),key=lambda x:x["fragment_id"]); merged=fragment_runtime.merge_fragments(fragments)
    native_by_id=_unique_map(inputs["native_outputs"],"fragment_id","compact native output")
    adapter_by_id=_unique_map(inputs["adapter_receipts"],"fragment_id","compact adapter receipt")
    if set(native_by_id)!=set(fragment_map) or set(adapter_by_id)!=set(fragment_map): raise ValueError("compact adapter denominator set mismatch")
    if merged["run_id"]!=run_id: raise ValueError("merged fragment run mismatch")
    disposition=_unique_map(merged["dispositions"],"obligation_id","disposition")
    if set(disposition)!=set(obligations): raise ValueError("disposition denominator set mismatch")
    facts=merged["facts"]
    fact_map={(x["obligation_id"],x["inventory_id"],x["line_start"],x["line_count"]):x for x in facts}
    if len(fact_map)!=len(facts): raise ValueError("duplicate R2 fact")
    fact_keys=set(fact_map)
    evidence_ok=sum(1 for x in facts if x["obligation_id"] in obligations and x["inventory_id"] in inventory_by_id
                    and obligations[x["obligation_id"]].get("inventory_id")==x["inventory_id"])
    referenced_receipts={rid for fragment in fragments for rid in fragment.get("skill_receipt_ids",[])}
    if set(receipts)!=referenced_receipts: raise ValueError("skill receipt set mismatch")
    binding_ok=0; assigned_obligations=[]
    for fragment in fragments:
        assignment=assignments[fragment["fragment_id"]]; assigned_obligations.extend(assignment.get("obligation_ids",[]))
        if (assignment.get("status")=="applied" and assignment.get("obligation_ids")==fragment.get("obligation_ids")
                and assignment.get("skill_receipt_ids")==fragment.get("skill_receipt_ids")): binding_ok+=1
    if len(assigned_obligations)!=len(set(assigned_obligations)) or set(assigned_obligations)!=set(obligations):
        raise ValueError("assignment obligation set mismatch")
    claims=[x for family in fragment_runtime.CONTRIBUTION_FAMILIES for x in merged["contributions"][family]]+merged["risk_cards"]
    claim_map={claim.get("contribution_id",claim.get("risk_id")):claim for claim in claims}
    if None in claim_map or len(claim_map)!=len(claims) or set(assessment_map)!=set(claim_map): raise ValueError("semantic assessment claim set mismatch")
    assessment_groups:dict[str,list[str]]={}
    for claim_id,assessment in assessment_map.items():
        receipt_hash=assessment.get("auditor_telemetry",{}).get("execution_receipt_sha256")
        if isinstance(receipt_hash,str): assessment_groups.setdefault(receipt_hash,[]).append(claim_id)
    batch_signed_ok:set[str]=set()
    for receipt_hash,ids in assessment_groups.items():
        signed=attestation_map.get(receipt_hash)
        if not signed: continue
        entries=[];decisions=[]
        for ordinal,claim_id in enumerate(sorted(ids)):
            claim=claim_map[claim_id];keys={tuple(key) for key in claim.get("fact_keys",[])}
            claim_fragment=next((fragment for fragment in fragments if any(
                item.get("contribution_id",item.get("risk_id"))==claim_id
                for family in fragment_runtime.CONTRIBUTION_FAMILIES for item in fragment["contributions"][family])
                or any(item.get("risk_id")==claim_id for item in fragment["risk_cards"])),None)
            if claim_fragment is None: break
            selected=[fact for fact in claim_fragment["facts"] if (fact.get("obligation_id"),fact.get("inventory_id"),fact.get("line_start"),fact.get("line_count")) in keys]
            entries.append({"ordinal":ordinal,"claim":claim,"facts":selected})
            assessment=assessment_map[claim_id];decisions.append([ordinal,assessment.get("supported"),assessment.get("reason")])
        receipt=signed[1];bindings=receipt.get("artifact_bindings",[])
        if (len(entries)==len(ids) and len(bindings)==1 and bindings[0].get("name")=="SEMANTIC_BATCH.json"
                and bindings[0].get("payload_sha256")==fragment_runtime.digest({"v":1,"claims":entries})
                and receipt.get("output_payload_sha256")==fragment_runtime.digest({"v":1,"a":decisions})):
            batch_signed_ok.add(receipt_hash)
    semantic_ok=0; referenced_attestations:set[str]=set()
    for claim_id,claim in claim_map.items():
        assessment=assessment_map[claim_id]; canonical={k:claim[k] for k in sorted(claim) if k not in {"contribution_id","risk_id"}}
        keys=claim.get("fact_keys",[])
        excerpts=[]; missing=False
        for key in keys:
            fact=fact_map.get(tuple(key)) if isinstance(key,list) else None
            if fact is None: missing=True; break
            excerpts.append(fact["excerpt_sha256"])
        auditor=assessment.get("auditor_telemetry",{}); receipt_hash=auditor.get("execution_receipt_sha256")
        signed=attestation_map.get(receipt_hash); referenced_attestations.add(receipt_hash) if isinstance(receipt_hash,str) else None
        claim_fragment=next((fragment for fragment in fragments if any(
            item.get("contribution_id",item.get("risk_id"))==claim_id
            for family in fragment_runtime.CONTRIBUTION_FAMILIES for item in fragment["contributions"][family])
            or any(item.get("risk_id")==claim_id for item in fragment["risk_cards"])),None)
        signed_ok=False
        if receipt_hash in batch_signed_ok and signed and claim_fragment:
            signed_ok=(signed[1].get("agent")=="auditor" and signed[1].get("session_id")==auditor.get("session_id"))
        elif signed and claim_fragment:
            receipt=signed[1]; bindings={row.get("name"):row.get("payload_sha256") for row in receipt.get("artifact_bindings",[])}
            signed_ok=(receipt.get("agent")=="auditor" and receipt.get("session_id")==auditor.get("session_id")
                       and receipt.get("output_payload_sha256")==fragment_runtime.digest({"supported":assessment.get("supported"),"reason":assessment.get("reason")})
                       and bindings=={"CLAIM.json":fragment_runtime.digest(claim),"FACTS.json":fragment_runtime.digest(claim_fragment["facts"])})
        if (not missing and signed_ok and assessment.get("claim_sha256")==fragment_runtime.digest(canonical)
                and assessment.get("fact_keys")==keys and assessment.get("supported") is True
                and assessment.get("source_excerpt_sha256s")==excerpts
                and _valid_auditor_telemetry(assessment.get("auditor_telemetry"))): semantic_ok+=1
    p0=[x for x in claims if x.get("priority")=="P0"]; p1=[x for x in claims if x.get("priority")=="P1"]
    priority_ok=lambda values:sum(1 for x in values if x.get("controls") and x.get("oracles") and all(tuple(k) in fact_keys for k in x["fact_keys"]))
    hc=[x for x in merged["risk_cards"] if x["severity"] in {"High","Critical"}]
    causal=("trigger","propagation","impact","observation","recovery","control","oracle")
    blackbox_ok=sum(1 for x in hc if all(str(x.get(k,"")).strip() for k in causal) and all(tuple(k) in fact_keys for k in x["fact_keys"]))
    nas=[x for x in disposition.values() if x["outcome"]=="not_applicable"]
    na_ok=sum(1 for x in nas if len(x.get("boundary","").strip())>=8 and len(x.get("reason","").strip())>=8
              and x.get("counterevidence_fact_keys") and all(tuple(k) in fact_keys for k in x["counterevidence_fact_keys"]))
    applicable=[oid for oid in obligations if disposition.get(oid,{}).get("outcome")!="not_applicable"]
    for oid,item in disposition.items():
        if item["outcome"]=="covered_by_other":
            current=oid; seen=set()
            while True:
                if current in seen: raise ValueError("covered_by is not a valid same-inventory closure")
                seen.add(current); current_disposition=disposition.get(current); current_row=obligations.get(current)
                if not current_disposition or not current_row or current_row.get("inventory_id")!=obligations[oid].get("inventory_id"):
                    raise ValueError("covered_by is not a valid same-inventory closure")
                if current_disposition.get("outcome")=="analyzed": break
                if current_disposition.get("outcome")!="covered_by_other" or not isinstance(current_disposition.get("covered_by"),str):
                    raise ValueError("covered_by is not a valid same-inventory closure")
                current=current_disposition["covered_by"]
        if item["outcome"]=="not_applicable" and any(tuple(k) not in fact_keys or k[0]!=oid for k in item.get("counterevidence_fact_keys",[])):
            raise ValueError("N/A counterevidence is not obligation-local")
    applicable_ok=sum(1 for oid in applicable if oid in disposition and disposition[oid]["outcome"] in {"analyzed","covered_by_other"})
    facts_by_obligation={oid:[fact for fact in facts if fact.get("obligation_id")==oid] for oid in obligations}
    anchored_items={fact.get("inventory_id") for fact in facts if isinstance(fact.get("evidence"),str) and len(fact["evidence"].encode())>=8}
    action_quality_ok=sum(1 for oid,row in obligations.items()
                          if disposition[oid].get("outcome") in {"analyzed","not_applicable"}
                          and len(str(disposition[oid].get("reason","")).encode())>=12
                          and bool(facts_by_obligation[oid]))
    action_quality_ok+=len(set(inventory_by_id)&anchored_items)
    action_quality_ok+=sum(1 for fragment in fragments if any(
        fragment["contributions"][family] for family in fragment_runtime.CONTRIBUTION_FAMILIES) or fragment["risk_cards"])
    action_quality_total=len(obligations)+len(inventory_by_id)+len(fragments)
    original_hc={(x["risk_id"],fragment["fragment_id"]) for fragment in fragments for x in fragment["risk_cards"] if x["severity"] in {"High","Critical"}}
    merged_hc={x["risk_id"] for x in hc}; retention_ok=sum(1 for risk_id,_ in original_hc if risk_id in merged_hc)
    telemetry_ok=0
    for fragment in fragments:
        assignment=assignments.get(fragment["fragment_id"]); telemetry=telemetry_by_id.get(fragment["fragment_id"])
        try:
            if not assignment or not telemetry: raise fragment_runtime.FragmentError("missing runner binding")
            fragment_runtime.validate_runner_telemetry(telemetry,fragment,assignment["candidate_sha256"])
            receipt_hash=telemetry["execution_receipt_sha256"]; referenced_attestations.add(receipt_hash)
            signed=attestation_map.get(receipt_hash)
            if not signed: raise fragment_runtime.FragmentError("missing signed worker execution")
            receipt=signed[1]; bindings={row.get("name"):row.get("payload_sha256") for row in receipt.get("artifact_bindings",[])}
            native=native_by_id[fragment["fragment_id"]];adapter=adapter_by_id[fragment["fragment_id"]]
            native_payload=native.get("native")
            if (receipt.get("agent")!="analysis-worker" or receipt.get("session_id")!=telemetry["session_id"]
                    or receipt.get("output_payload_sha256")!=fragment_runtime.digest(native_payload)
                    or set(bindings)!={"COMPACT_CONTEXT.json"}
                    or adapter.get("native_output_sha256")!=fragment_runtime.digest(native_payload)
                    or adapter.get("expanded_fragment_sha256")!=fragment_runtime.digest(fragment)
                    or adapter.get("execution_receipt_sha256")!=receipt_hash):
                raise fragment_runtime.FragmentError("worker execution binding mismatch")
            telemetry_ok+=1
        except fragment_runtime.FragmentError:
            pass
    if referenced_attestations!=set(attestation_map): raise ValueError("execution attestation set mismatch")
    metrics={"evidence_refs":_rate(evidence_ok+binding_ok,len(facts)+len(fragments)),
             "action_quality":_rate(action_quality_ok,action_quality_total),"semantic_support":_rate(semantic_ok,len(claims)),
             "p0":_rate(priority_ok(p0),len(p0)),"p1":_rate(priority_ok(p1),len(p1)),"blackbox":_rate(blackbox_ok,len(hc)),
             "na_specificity":_rate(na_ok,len(nas)),"applicable_disposition":_rate(applicable_ok,len(applicable)),
             "hc_retention":_rate(retention_ok,len(original_hc)),"telemetry":_rate(telemetry_ok,len(fragments))}
    findings={name:[] if value>=R2_GATES[name] else [f"{name}={value:.2f} < {R2_GATES[name]:.2f}"] for name,value in metrics.items()}
    checks={name:{"verdict":"PASS" if not rows else "FAIL","findings":rows} for name,rows in findings.items()}
    verdict="PASS" if all(x["verdict"]=="PASS" for x in checks.values()) else "FAIL"
    expected_bindings=_expected_artifact_bindings(inputs); bindings=inputs["artifact_bindings"]
    if (bindings!=expected_bindings or any(set(x)!={"artifact_type","artifact_id","payload_sha256"}
            or not x["artifact_type"] or not x["artifact_id"] or not _is_hash(x["payload_sha256"]) for x in bindings)):
        raise ValueError("artifact binding closure mismatch")
    return {"artifact_type":"coverage_judge_r2","schema_version":"1.0","run_id":run_id,"verdict":verdict,
            "denominator":{"repositories":len(inventories),"obligations":len(obligations),"fragments":len(fragments)},
            "thresholds":dict(R2_GATES),"metrics":metrics,"checks":checks,"merged_sha256":merged["sha256"]}

def _is_hash(value:Any)->bool:
    return isinstance(value,str) and len(value)==64 and all(c in "0123456789abcdef" for c in value)

def _valid_auditor_telemetry(value:Any)->bool:
    required={"model","input_tokens","output_tokens","finish_reason","valid_json","captured_by","session_id","execution_receipt_sha256"}
    return (isinstance(value,dict) and set(value)==required and value.get("model")=="deepseek/deepseek-v4-flash"
            and type(value.get("input_tokens")) is int and 1<=value["input_tokens"]<=180000
            and type(value.get("output_tokens")) is int and 1<=value["output_tokens"]<=4096
            and value.get("finish_reason")=="stop" and value.get("valid_json") is True and value.get("captured_by")=="opencode-runner"
            and isinstance(value.get("session_id"),str) and bool(value["session_id"])
            and _is_hash(value.get("execution_receipt_sha256")))
