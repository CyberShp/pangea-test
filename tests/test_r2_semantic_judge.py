from __future__ import annotations
import copy, json, unittest
from pathlib import Path
import jsonschema
from runtime import compact_protocol, coverage_judge, fragment_runtime
from evaluation import benchmark
from tests.role_execution_fixtures import signed_role_attestation, resign_role_attestation

OID="OBL-"+"1"*16; IID="INV-"+"2"*16; KEY=[OID,IID,1,1]
OID2="OBL-"+"3"*16; IID2="INV-"+"4"*16; KEY2=[OID2,IID2,1,1]

def contribution(priority="P0"):
    payload={"priority":priority,"obligation_id":OID,"fact_keys":[KEY],"summary":"request state transition",
             "controls":["send invalid then valid request"],"oracles":["second request succeeds"]}
    return {"contribution_id":fragment_runtime._canonical_id("C-",payload),**payload}

def risk():
    payload={"severity":"High","obligation_id":OID,"fact_keys":[KEY],"summary":"state remains failed",
             "trigger":"invalid request","propagation":"error leaves state set","impact":"next request fails",
             "observation":"return and state log","recovery":"valid request restores state",
             "control":"send invalid then valid request","oracle":"first fails and second succeeds"}
    return {"risk_id":fragment_runtime._canonical_id("R-",payload),**payload}

def fragment():
    families={name:[] for name in fragment_runtime.CONTRIBUTION_FAMILIES}; families["flows"]=[contribution()]
    return {"artifact_type":"analysis_fragment","schema_version":"2.0","worker_instance":"analysis-worker","run_id":"run",
            "fragment_id":"frag-1","context_pack_sha256":"3"*64,"obligation_ids":[OID],"skill_receipt_ids":[],
            "facts":[{"obligation_id":OID,"inventory_id":IID,"path":"x.c","line_start":1,"line_count":1,
                      "excerpt_sha256":"4"*64,"evidence":"bounded source"}],"contributions":families,"risk_cards":[risk()],
            "dispositions":[{"obligation_id":OID,"outcome":"analyzed","reason":"source inspected"}],"unresolved":[],
            "usage":{"output_tokens":10,"finish_reason":"stop","valid_json":True}}

def _compact_context(fragment_id, item_ordinal, action_ordinal, path, action):
    return {"v":1,"f":fragment_id,"s":[[item_ordinal,path,1,1,"bounded source line"]],"k":[],
            "i":[[item_ordinal,[[action_ordinal,action]]]],"q":{
                "evidence_bytes":[compact_protocol.EVIDENCE_MIN_BYTES,compact_protocol.EVIDENCE_MAX_BYTES],
                "semantic_bytes":[compact_protocol.SEMANTIC_MIN_BYTES,compact_protocol.SEMANTIC_MAX_BYTES],
                "claim_bytes":[compact_protocol.CLAIM_MIN_BYTES,compact_protocol.CLAIM_MAX_BYTES],
                "claim_limit":compact_protocol.WORKER_CLAIM_LIMIT,"rich_risk_limit":compact_protocol.RICH_RISK_LIMIT,
                "claim_forms":{"C":["C","family","priority","action","summary","control","oracle"],
                               "R":["R","severity","action","summary","trigger","propagation","impact","observation","recovery","control","oracle"]},
                "families":compact_protocol.FAMILY_CODES,"priorities":sorted(compact_protocol.PRIORITIES),
                "risk_severities":sorted(compact_protocol.RISK_SEVERITIES),"outcomes":sorted(compact_protocol.OUTCOMES)}}

def inputs():
    commit="7"*40
    inventory={"repository":"repo","commit":commit,"items":[
        {"inventory_id":IID,"path":"x.c","line_start":1,"line_end":1},
        {"inventory_id":IID2,"path":"y.c","line_start":1,"line_end":1}]}
    ledger={"repository":"repo","obligations":[
        {"obligation_id":OID,"inventory_id":IID,"action":"explain branch"},
        {"obligation_id":OID2,"inventory_id":IID2,"action":"disconfirm state"}]}
    mapping=compact_protocol.ordinal_map(inventory,ledger)
    rows=(
        (0,0,OID,"x.c",["C","f","P0",0,"request state transition","send invalid request","second request succeeds"]),
        (1,1,OID2,"y.c",["R","High",1,"state remains failed","invalid request sent","failure state persists",
                          "next request fails","return and state log","valid request restores",
                          "send invalid then valid","first fails then succeeds"]),
    )
    assignments=[]; fragments=[]; native_outputs=[]; adapter_receipts=[]; telemetry=[]; attestations=[]
    contexts={}
    for index,(item_ordinal,action_ordinal,oid,path,claim) in enumerate(rows):
        fragment_id=compact_protocol.fragment_identity("run","repo",commit,mapping,[item_ordinal])
        compact=_compact_context(fragment_id,item_ordinal,action_ordinal,path,ledger["obligations"][index]["action"])
        pack={"run_id":"run","repository":"repo","commit":commit,"fragment_id":fragment_id,
              "obligation_ids":[oid],"skill_receipts":[]}
        native={"v":1,"i":[[item_ordinal,"bounded source proof"]],
                "a":[[action_ordinal,"A","source transition found"]],"c":[claim]}
        expanded=compact_protocol.expand_native(native,compact,mapping,pack)
        candidate_sha=fragment_runtime.digest({"compact_context":compact,"ordinal_map":mapping,"context_pack":pack})
        assignment={"fragment_id":fragment_id,"candidate_sha256":candidate_sha,"status":"applied",
                    "obligation_ids":[oid],"skill_receipt_ids":[]}
        worker_att=signed_role_attestation("analysis-worker",native,{"COMPACT_CONTEXT.json":compact},f"ses_worker_{index}")
        worker_att["receipt"]["model_call_limit"]=1
        worker_att=resign_role_attestation(worker_att); worker_hash=fragment_runtime.digest(worker_att["receipt"])
        assignments.append(assignment); fragments.append(expanded); contexts[fragment_id]=compact
        native_outputs.append({"artifact_type":"compact_native_output","schema_version":"1.0",
                               "fragment_id":fragment_id,"native":native})
        adapter_receipts.append({"artifact_type":"compact_adapter_receipt","schema_version":"1.0",
            "fragment_id":fragment_id,"native_output_sha256":fragment_runtime.digest(native),
            "adapter_version":compact_protocol.VERSION,"ordinal_map_sha256":fragment_runtime.digest(mapping),
            "expanded_fragment_sha256":fragment_runtime.digest(expanded),"execution_receipt_sha256":worker_hash})
        telemetry.append({"artifact_type":"runner_telemetry","schema_version":"1.0","run_id":"run","fragment_id":fragment_id,
            "model":"deepseek/deepseek-v4-flash","candidate_sha256":candidate_sha,"fragment_sha256":fragment_runtime.digest(expanded),
            "context_sha256":fragment_runtime.digest(compact),"session_id":f"ses_worker_{index}","execution_receipt_sha256":worker_hash,
            "input_tokens":100,"output_tokens":10,"finish_reason":"stop","valid_json":True,"captured_by":"opencode-runner"})
        attestations.append(worker_att)
    claims=[]
    for expanded in fragments:
        fragment_claims=[item for family in fragment_runtime.CONTRIBUTION_FAMILIES for item in expanded["contributions"][family]]
        fragment_claims+=expanded["risk_cards"]
        for claim in fragment_claims:
            keys={tuple(key) for key in claim["fact_keys"]}
            facts=[fact for fact in expanded["facts"] if (fact["obligation_id"],fact["inventory_id"],fact["line_start"],fact["line_count"]) in keys]
            claims.append((claim.get("contribution_id",claim.get("risk_id")),claim,facts))
    claims.sort(key=lambda row:row[0])
    batch={"v":1,"claims":[{"ordinal":ordinal,"claim":claim,"facts":facts}
                            for ordinal,(_,claim,facts) in enumerate(claims)]}
    audit_native={"v":1,"a":[[ordinal,True,"source fact supports claim"] for ordinal in range(len(claims))]}
    auditor_att=signed_role_attestation("auditor",audit_native,{"SEMANTIC_BATCH.json":batch},"ses_audit")
    auditor_att["receipt"]["model_call_limit"]=1
    auditor_att=resign_role_attestation(auditor_att); auditor_hash=fragment_runtime.digest(auditor_att["receipt"])
    attestations.append(auditor_att); assessments=[]
    for claim_id,claim,facts in claims:
        canonical={k:claim[k] for k in sorted(claim) if k not in {"contribution_id","risk_id"}}
        assessments.append({"artifact_type":"semantic_assessment","schema_version":"1.0","claim_id":claim_id,
            "claim_sha256":fragment_runtime.digest(canonical),"fact_keys":claim["fact_keys"],
            "source_excerpt_sha256s":[fact["excerpt_sha256"] for fact in facts],"supported":True,
            "reason":"source fact supports claim","auditor_telemetry":{"model":"deepseek/deepseek-v4-flash",
            "input_tokens":100,"output_tokens":20,"finish_reason":"stop","valid_json":True,
            "captured_by":"opencode-runner","session_id":"ses_audit","execution_receipt_sha256":auditor_hash}})
    value={"run_id":"run","inventories":[inventory],"ledgers":[ledger],"assignments":assignments,
            "fragments":fragments,"native_outputs":native_outputs,"adapter_receipts":adapter_receipts,
            "skill_receipts":[],"telemetry":telemetry,"semantic_assessments":assessments,
            "publication_manifests":[{"status":"committed","artifacts":[]},{"status":"committed","assignments":[],"contexts":[]}],
            "execution_attestations":attestations,"artifact_bindings":[]}
    value["artifact_bindings"]=coverage_judge._expected_artifact_bindings(value)
    return value

def refresh(value):
    value["artifact_bindings"]=coverage_judge._expected_artifact_bindings(value); return value

def claim_fragment(value, kind):
    if kind=="contribution":
        return next(fragment for fragment in value["fragments"] if any(
            fragment["contributions"][family] for family in fragment_runtime.CONTRIBUTION_FAMILIES))
    return next(fragment for fragment in value["fragments"] if fragment["risk_cards"])

def rebind_auditor(value, mutate):
    index=next(index for index,attestation in enumerate(value["execution_attestations"])
               if attestation["receipt"]["execution_agent"]=="audit-leaf")
    attestation=copy.deepcopy(value["execution_attestations"][index]); old_hash=fragment_runtime.digest(attestation["receipt"])
    mutate(attestation["receipt"]); attestation=resign_role_attestation(attestation)
    new_hash=fragment_runtime.digest(attestation["receipt"]); value["execution_attestations"][index]=attestation
    for assessment in value["semantic_assessments"]:
        if assessment["auditor_telemetry"]["execution_receipt_sha256"]==old_hash:
            assessment["auditor_telemetry"]["execution_receipt_sha256"]=new_hash
    return refresh(value)

def rebind_worker(value, mutate):
    index=next(index for index,attestation in enumerate(value["execution_attestations"])
               if attestation["receipt"]["execution_agent"]=="analysis-leaf")
    attestation=copy.deepcopy(value["execution_attestations"][index]); old_hash=fragment_runtime.digest(attestation["receipt"])
    mutate(attestation["receipt"]); attestation=resign_role_attestation(attestation)
    new_hash=fragment_runtime.digest(attestation["receipt"]); value["execution_attestations"][index]=attestation
    for adapter in value["adapter_receipts"]:
        if adapter["execution_receipt_sha256"]==old_hash: adapter["execution_receipt_sha256"]=new_hash
    for telemetry in value["telemetry"]:
        if telemetry["execution_receipt_sha256"]==old_hash: telemetry["execution_receipt_sha256"]=new_hash
    return refresh(value)

class R2SemanticJudgeTests(unittest.TestCase):
    def test_signed_execution_budget_contract_is_exact(self):
        attestation=signed_role_attestation("analysis-worker",{"ok":True},{"CONTEXT.json":{"ok":True}},"ses_budget")
        schema=json.loads((Path(__file__).parents[1]/"schemas/role-execution-attestation.schema.json").read_text())
        jsonschema.validate(attestation,schema)
        fragment_runtime.verify_execution_attestation(attestation,"analysis-worker")
        mutations=(
            ("model_call_limit",True),
            ("model_call_limit",-1),
            ("model_calls_completed",41),
            ("model_requests_admitted",0),
            ("pre_request_budget_blocked",True),
            ("pre_request_budget_enforced",False),
        )
        for field,bad in mutations:
            invalid=copy.deepcopy(attestation); invalid["receipt"][field]=bad
            with self.subTest(field=field,bad=bad):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(invalid,schema)
                with self.assertRaises(fragment_runtime.FragmentError):
                    fragment_runtime.verify_execution_attestation(resign_role_attestation(invalid),"analysis-worker")
        for name,limit,calls,admitted,injected in (
            ("zero-limit-zero-calls",0,0,0,False),
            ("positive-limit-zero-calls",40,0,0,False),
            ("injected-zero-calls",40,0,0,True),
        ):
            invalid=copy.deepcopy(attestation)
            invalid["receipt"].update({
                "model_call_limit":limit,
                "model_calls_completed":calls,
                "model_requests_admitted":admitted,
                "injected_test_runner":injected,
                "pre_request_budget_enforced":not injected,
                "evidence_class":"test-only" if injected else "production",
            })
            with self.subTest(name=name):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(invalid,schema)
                with self.assertRaises(fragment_runtime.FragmentError):
                    fragment_runtime.verify_execution_attestation(
                        resign_role_attestation(invalid),"analysis-worker",
                    )
        injected=copy.deepcopy(attestation)
        injected["receipt"]["injected_test_runner"]=True
        injected["receipt"]["pre_request_budget_enforced"]=False
        injected["receipt"]["evidence_class"]="test-only"
        fragment_runtime.verify_execution_attestation(resign_role_attestation(injected),"analysis-worker")
        mismatched=copy.deepcopy(attestation)
        mismatched["receipt"]["resolved_plugin_closure"]["exact"]=False
        with self.assertRaises(fragment_runtime.FragmentError):
            fragment_runtime.verify_execution_attestation(resign_role_attestation(mismatched),"analysis-worker")
        nested_extra=copy.deepcopy(attestation)
        nested_extra["receipt"]["plugin_closure"]["untrusted_plugin_detail"]=0
        with self.assertRaises(fragment_runtime.FragmentError):
            fragment_runtime.verify_execution_attestation(resign_role_attestation(nested_extra),"analysis-worker")
        extra=copy.deepcopy(attestation); extra["receipt"]["untrusted_budget_detail"]=0
        with self.assertRaises(fragment_runtime.FragmentError):
            fragment_runtime.verify_execution_attestation(resign_role_attestation(extra),"analysis-worker")

    def test_full_supported_chain_passes_all_gates_and_schema(self):
        result=coverage_judge.judge_r2(inputs()); self.assertEqual("PASS",result["verdict"])
        result["input_artifacts"]=[{"path":"internal/x.json","sha256":"6"*64}]
        schema=json.loads((Path(__file__).parents[1]/"schemas/coverage-judge-r2.schema.json").read_text())
        jsonschema.validate(result,schema)

    def test_compact_alias_cap40_fails_after_all_dependent_hashes_are_rebound(self):
        schema=json.loads((Path(__file__).parents[1]/"schemas/role-execution-attestation.schema.json").read_text())
        for alias,rebind in (("analysis-leaf",rebind_worker),("audit-leaf",rebind_auditor)):
            value=inputs(); value=rebind(value,lambda receipt:receipt.update(model_call_limit=40))
            attestation=next(attestation for attestation in value["execution_attestations"]
                             if attestation["receipt"]["execution_agent"]==alias)
            with self.subTest(alias=alias):
                with self.assertRaises(jsonschema.ValidationError): jsonschema.validate(attestation,schema)
                with self.assertRaisesRegex(ValueError,"compact role model budget"):
                    coverage_judge.judge_r2(value)

    def test_pure_judge_rejects_unverifiable_filesystem_claims(self):
        value=inputs(); value["input_artifacts"]=[{"path":"internal/x.json","sha256":"6"*64}]
        with self.assertRaisesRegex(ValueError,"input contract"): coverage_judge.judge_r2(value)

    def test_worker_usage_cannot_replace_runner_telemetry(self):
        value=inputs(); claim_fragment(value,"contribution")["usage"]["output_tokens"]=1; value["telemetry"]=[]
        with self.assertRaisesRegex(ValueError,"set mismatch"): coverage_judge.judge_r2(value)

    def test_missing_disposition_and_weak_hc_chain_fail(self):
        value=inputs(); claim_fragment(value,"contribution")["dispositions"]=[]
        claim_fragment(value,"risk")["risk_cards"][0].pop("oracle")
        with self.assertRaisesRegex(ValueError,"disposition denominator"): coverage_judge.judge_r2(value)

    def test_merge_is_deterministic_retains_hc_and_blocks_conflict(self):
        first=fragment(); second=copy.deepcopy(first); second["fragment_id"]="frag-2"; second["obligation_ids"]=[]; second["facts"]=[]
        second["contributions"]={name:[] for name in fragment_runtime.CONTRIBUTION_FAMILIES}; second["risk_cards"]=[]; second["dispositions"]=[]
        a=fragment_runtime.merge_fragments([first,second]); b=fragment_runtime.merge_fragments([second,first]); self.assertEqual(a,b)
        self.assertEqual([risk()["risk_id"]],[x["risk_id"] for x in a["risk_cards"]])
        conflict=copy.deepcopy(first); conflict["fragment_id"]="frag-3"; conflict["risk_cards"][0]["summary"]="different"
        with self.assertRaises(fragment_runtime.FragmentError): fragment_runtime.merge_fragments([first,conflict])

    def test_telemetry_hash_and_finish_reason_are_runner_owned(self):
        value=inputs(); telemetry=value["telemetry"][0]
        for field,bad in (("fragment_sha256","0"*64),("finish_reason","tool"),("output_tokens",4097)):
            mutated=copy.deepcopy(telemetry); mutated[field]=bad
            with self.assertRaises(fragment_runtime.FragmentError): fragment_runtime.validate_runner_telemetry(mutated,value["fragments"][0],"5"*64)

    def test_rejects_duplicate_repository(self):
        value=inputs(); value["inventories"].append(copy.deepcopy(value["inventories"][0]))
        with self.assertRaises(ValueError): coverage_judge.judge_r2(value)

    def test_rejects_duplicate_inventory_id(self):
        value=inputs(); value["inventories"][0]["items"].append(copy.deepcopy(value["inventories"][0]["items"][0]))
        with self.assertRaises(ValueError): coverage_judge.judge_r2(value)

    def test_rejects_duplicate_obligation_id(self):
        value=inputs(); value["ledgers"][0]["obligations"].append(copy.deepcopy(value["ledgers"][0]["obligations"][0]))
        with self.assertRaises(ValueError): coverage_judge.judge_r2(value)

    def test_rejects_duplicate_assignment(self):
        value=inputs(); value["assignments"].append(copy.deepcopy(value["assignments"][0]))
        with self.assertRaises(ValueError): coverage_judge.judge_r2(value)

    def test_rejects_duplicate_fragment(self):
        value=inputs(); value["fragments"].append(copy.deepcopy(value["fragments"][0]))
        with self.assertRaises((ValueError,fragment_runtime.FragmentError)): coverage_judge.judge_r2(value)

    def test_rejects_extra_unreferenced_receipt(self):
        value=inputs(); value["skill_receipts"].append({"receipt_id":"SR-"+"9"*16})
        with self.assertRaises(ValueError): coverage_judge.judge_r2(refresh(value))

    def test_rejects_missing_telemetry(self):
        value=inputs(); value["telemetry"]=[]
        with self.assertRaises(ValueError): coverage_judge.judge_r2(refresh(value))

    def test_rejects_extra_semantic_assessment(self):
        value=inputs(); extra=copy.deepcopy(value["semantic_assessments"][0]); extra["claim_id"]="C-"+"9"*16; value["semantic_assessments"].append(extra)
        with self.assertRaises(ValueError): coverage_judge.judge_r2(refresh(value))

    def test_rejects_missing_semantic_assessment(self):
        value=inputs(); value["semantic_assessments"].pop()
        with self.assertRaises(ValueError): coverage_judge.judge_r2(refresh(value))

    def test_rejects_missing_or_extra_compact_native_and_adapter_artifacts(self):
        for collection in ("native_outputs","adapter_receipts"):
            missing=inputs(); missing[collection].pop()
            with self.subTest(collection=collection,mutation="missing"):
                with self.assertRaisesRegex(ValueError,"compact adapter denominator"):
                    coverage_judge.judge_r2(refresh(missing))
            extra=inputs(); row=copy.deepcopy(extra[collection][0]); row["fragment_id"]="frag-extra"; extra[collection].append(row)
            with self.subTest(collection=collection,mutation="extra"):
                with self.assertRaisesRegex(ValueError,"compact adapter denominator"):
                    coverage_judge.judge_r2(refresh(extra))

    def test_rejects_compact_native_expanded_and_execution_hash_drift(self):
        for field in ("native_output_sha256","expanded_fragment_sha256","execution_receipt_sha256"):
            value=inputs(); value["adapter_receipts"][0][field]="0"*64
            with self.subTest(field=field):
                result=coverage_judge.judge_r2(refresh(value))
                self.assertEqual("FAIL",result["verdict"])
                self.assertLess(result["metrics"]["telemetry"],100.0)

    def test_rejects_missing_or_reordered_semantic_batch_attestation(self):
        missing=inputs(); missing["execution_attestations"]=[attestation for attestation in missing["execution_attestations"]
            if attestation["receipt"]["execution_agent"]!="audit-leaf"]
        with self.assertRaisesRegex(ValueError,"execution attestation set mismatch"):
            coverage_judge.judge_r2(refresh(missing))
        reordered=inputs()
        fragments=sorted(reordered["fragments"],key=lambda row:row["fragment_id"])
        claims=[]
        for fragment_value in fragments:
            fragment_claims=[claim for family in fragment_runtime.CONTRIBUTION_FAMILIES
                             for claim in fragment_value["contributions"][family]]+fragment_value["risk_cards"]
            for claim in fragment_claims:
                keys={tuple(key) for key in claim["fact_keys"]}
                facts=[fact for fact in fragment_value["facts"] if
                       (fact["obligation_id"],fact["inventory_id"],fact["line_start"],fact["line_count"]) in keys]
                claims.append((claim.get("contribution_id",claim.get("risk_id")),claim,facts))
        claims.sort(key=lambda row:row[0])
        batch={"v":1,"claims":[{"ordinal":ordinal,"claim":claim,"facts":facts}
                                for ordinal,(_,claim,facts) in enumerate(claims)]}
        batch["claims"].reverse()
        reordered=rebind_auditor(reordered,lambda receipt: receipt["artifact_bindings"][0].update(
            payload_sha256=fragment_runtime.digest(batch)))
        result=coverage_judge.judge_r2(reordered)
        self.assertEqual("FAIL",result["verdict"])
        self.assertLess(result["metrics"]["semantic_support"],100.0)

    def test_rejects_self_declared_execution_hash_and_tampered_signature(self):
        value=inputs(); value["semantic_assessments"][0]["auditor_telemetry"]["execution_receipt_sha256"]="f"*64
        with self.assertRaises(ValueError): coverage_judge.judge_r2(refresh(value))
        value=inputs(); value["execution_attestations"][0]["signature"]="0"*128
        with self.assertRaisesRegex(ValueError,"signature"): coverage_judge.judge_r2(refresh(value))

    def test_rejects_missing_payload_binding(self):
        value=inputs(); value["artifact_bindings"].pop()
        with self.assertRaises(ValueError): coverage_judge.judge_r2(value)

    def test_rejects_extra_payload_binding(self):
        value=inputs(); value["artifact_bindings"].append({"artifact_type":"extra","artifact_id":"x","payload_sha256":"0"*64})
        with self.assertRaises(ValueError): coverage_judge.judge_r2(value)

    def test_rejects_unused_inventory_item(self):
        value=inputs(); value["inventories"][0]["items"].append({"inventory_id":"INV-"+"7"*16})
        with self.assertRaisesRegex(ValueError,"unused item"): coverage_judge.judge_r2(refresh(value))

    def test_rejects_ghost_repository_with_empty_denominator(self):
        value=inputs(); value["inventories"].append({"repository":"ghost","items":[]})
        value["ledgers"].append({"repository":"ghost","obligations":[]})
        with self.assertRaisesRegex(ValueError,"must not be empty"): coverage_judge.judge_r2(refresh(value))

    def test_covered_by_missing_target_is_stable_value_error(self):
        value=inputs(); contribution_value=claim_fragment(value,"contribution")
        contribution_value["dispositions"]=[{"obligation_id":OID,"outcome":"covered_by_other","reason":"same item","covered_by":"OBL-"+"8"*16}]
        with self.assertRaisesRegex(ValueError,"covered_by"): coverage_judge.judge_r2(value)

    def test_na_missing_own_fact_is_stable_value_error(self):
        value=inputs(); contribution_value=claim_fragment(value,"contribution")
        contribution_value["dispositions"]=[{"obligation_id":OID,"outcome":"not_applicable","reason":"bounded source excludes behavior","boundary":"selected source boundary","counterevidence_fact_keys":[[OID,IID,2,1]]}]
        with self.assertRaisesRegex(ValueError,"counterevidence"): coverage_judge.judge_r2(value)

if __name__=="__main__": unittest.main()
