from __future__ import annotations
import copy, json, unittest
from pathlib import Path
import jsonschema
from runtime import coverage_judge, fragment_runtime
from evaluation import benchmark
from tests.role_execution_fixtures import signed_role_attestation, resign_role_attestation

OID="OBL-"+"1"*16; IID="INV-"+"2"*16; KEY=[OID,IID,1,1]

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

def inputs():
    f=fragment(); assignment={"fragment_id":"frag-1","candidate_sha256":"5"*64,"status":"applied",
                              "obligation_ids":[OID],"skill_receipt_ids":[]}
    worker_att=signed_role_attestation("analysis-worker",f,{"CONTEXT.json":"context-placeholder"},"ses_worker")
    worker_att["receipt"]["artifact_bindings"][0]["payload_sha256"]="6"*64
    raw=json.dumps(worker_att["receipt"],sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    worker_att["signature"]=benchmark._EXECUTION_PRIVATE_KEY.sign(raw).hex(); worker_hash=fragment_runtime.digest(worker_att["receipt"])
    telemetry={"artifact_type":"runner_telemetry","schema_version":"1.0","run_id":"run","fragment_id":"frag-1",
               "model":"deepseek/deepseek-v4-flash","candidate_sha256":"5"*64,"fragment_sha256":fragment_runtime.digest(f),
               "context_sha256":"6"*64,"session_id":"ses_worker","execution_receipt_sha256":worker_hash,
               "input_tokens":100,"output_tokens":10,"finish_reason":"stop","valid_json":True,"captured_by":"opencode-runner"}
    claims=[contribution(),risk()]; assessments=[]; attestations=[worker_att]
    for claim in claims:
        claim_id=claim.get("contribution_id",claim.get("risk_id")); canonical={k:claim[k] for k in sorted(claim) if k not in {"contribution_id","risk_id"}}
        decision={"supported":True,"reason":"source excerpt directly supports claim"}
        auditor_att=signed_role_attestation("auditor",decision,{"CLAIM.json":claim,"FACTS.json":f["facts"]},"ses_"+claim_id)
        auditor_hash=fragment_runtime.digest(auditor_att["receipt"]); attestations.append(auditor_att)
        assessments.append({"artifact_type":"semantic_assessment","schema_version":"1.0","claim_id":claim_id,
            "claim_sha256":fragment_runtime.digest(canonical),"fact_keys":claim["fact_keys"],"source_excerpt_sha256s":["4"*64],
            **decision,"auditor_telemetry":{"model":"deepseek/deepseek-v4-flash","input_tokens":100,"output_tokens":20,"finish_reason":"stop","valid_json":True,"captured_by":"opencode-runner","session_id":"ses_"+claim_id,"execution_receipt_sha256":auditor_hash}})
    value={"run_id":"run","inventories":[{"repository":"repo","items":[{"inventory_id":IID}]}],
            "ledgers":[{"repository":"repo","obligations":[{"obligation_id":OID,"inventory_id":IID}]}],
            "assignments":[assignment],"fragments":[f],"skill_receipts":[],"telemetry":[telemetry],"semantic_assessments":assessments,
            "publication_manifests":[{"status":"committed","artifacts":[]},{"status":"committed","assignments":[],"contexts":[]}],
            "execution_attestations":attestations,"artifact_bindings":[]}
    value["artifact_bindings"]=coverage_judge._expected_artifact_bindings(value)
    return value

def refresh(value):
    value["artifact_bindings"]=coverage_judge._expected_artifact_bindings(value); return value

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

    def test_pure_judge_rejects_unverifiable_filesystem_claims(self):
        value=inputs(); value["input_artifacts"]=[{"path":"internal/x.json","sha256":"6"*64}]
        with self.assertRaisesRegex(ValueError,"input contract"): coverage_judge.judge_r2(value)

    def test_worker_usage_cannot_replace_runner_telemetry(self):
        value=inputs(); value["fragments"][0]["usage"]["output_tokens"]=1; value["telemetry"]=[]
        with self.assertRaisesRegex(ValueError,"set mismatch"): coverage_judge.judge_r2(value)

    def test_missing_disposition_and_weak_hc_chain_fail(self):
        value=inputs(); value["fragments"][0]["dispositions"]=[]; value["fragments"][0]["risk_cards"][0].pop("oracle")
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
        value=inputs(); value["fragments"][0]["dispositions"]=[{"obligation_id":OID,"outcome":"covered_by_other","reason":"same item","covered_by":"OBL-"+"8"*16}]
        with self.assertRaisesRegex(ValueError,"covered_by"): coverage_judge.judge_r2(value)

    def test_na_missing_own_fact_is_stable_value_error(self):
        value=inputs(); value["fragments"][0]["dispositions"]=[{"obligation_id":OID,"outcome":"not_applicable","reason":"bounded source excludes behavior","boundary":"selected source boundary","counterevidence_fact_keys":[[OID,IID,2,1]]}]
        with self.assertRaisesRegex(ValueError,"counterevidence"): coverage_judge.judge_r2(value)

if __name__=="__main__": unittest.main()
