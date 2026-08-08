from __future__ import annotations
import copy, hashlib, json, unittest
from pathlib import Path
import jsonschema
from runtime import compact_protocol, context_budget, coverage_judge, fragment_runtime
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
    inventory={"repository":"repo","commit":commit,"snapshot_sha256":"8"*64,"items":[
        {"inventory_id":IID,"path":"x.c","line_start":1,"line_end":1,"storage_skill_triggers":[]},
        {"inventory_id":IID2,"path":"y.c","line_start":1,"line_end":1,"storage_skill_triggers":[]}]}
    ledger={"artifact_type":"obligation_ledger","schema_version":"1.0","repository":"repo",
            "commit":commit,"snapshot_sha256":inventory["snapshot_sha256"],
            "inventory_sha256":fragment_runtime.digest(inventory),"obligations":[
        {"obligation_id":OID,"inventory_id":IID,"action":"explain branch","status":"pending",
         "assigned_fragment_id":None,"assigned_worker_id":None,"skill_receipt_ids":[],"evidence":[],
         "disposition":None,"unresolved":[]},
        {"obligation_id":OID2,"inventory_id":IID2,"action":"disconfirm state","status":"pending",
         "assigned_fragment_id":None,"assigned_worker_id":None,"skill_receipt_ids":[],"evidence":[],
         "disposition":None,"unresolved":[]}]}
    mapping=compact_protocol.ordinal_map(inventory,ledger)
    rows=(
        (0,0,OID,"x.c",["C","f","P0",0,"request state transition","send invalid request","second request succeeds"]),
        (1,1,OID2,"y.c",["R","High",1,"state remains failed","invalid request sent","failure state persists",
                          "next request fails","return and state log","valid request restores",
                          "send invalid then valid","first fails then succeeds"]),
    )
    assignments=[]; fragments=[]; context_artifacts=[]; native_outputs=[]; adapter_receipts=[]; telemetry=[]; attestations=[]
    for index,(item_ordinal,action_ordinal,oid,path,claim) in enumerate(rows):
        fragment_id=compact_protocol.fragment_identity("run","repo",commit,mapping,[item_ordinal])
        compact=_compact_context(fragment_id,item_ordinal,action_ordinal,path,ledger["obligations"][index]["action"])
        source_text=compact["s"][0][4]
        allowed_ranges=[{"inventory_ids":[IID if index==0 else IID2],"path":path,
                         "line_start":1,"line_end":1}]
        injected={"sources":[{"path":path,"line_start":1,"line_end":1,
                              "inventory_ids":[IID if index==0 else IID2],
                              "sha256":hashlib.sha256(source_text.encode()).hexdigest(),"text":source_text}],
                  "skills":[]}
        pack={"artifact_type":"context_pack","schema_version":context_budget.SCHEMA_VERSION,
              "worker":"analysis-worker","run_id":"run","repository":"repo","commit":commit,
              "fragment_id":fragment_id,"snapshot_sha256":inventory["snapshot_sha256"],
              "inventory_sha256":context_budget.digest(inventory),"ledger_sha256":context_budget.digest(ledger),
              "obligation_ids":[oid],"skill_receipts":[],"allowed_ranges":allowed_ranges,
              "content_digests":context_budget._digests(injected),"input_budget_tokens":0,
              "output_budget_tokens":context_budget.OUTPUT_RESERVED_TOKENS,"budget_receipt":{}}
        pack["input_budget_tokens"],pack["budget_receipt"]=context_budget._budget_receipt(pack,injected)
        native={"v":1,"i":[[item_ordinal,"bounded source proof"]],
                "a":[[action_ordinal,"A","source transition found"]],"c":[claim]}
        canonical_native=compact_protocol.canonicalize_native(native,compact)
        expanded=compact_protocol.expand_native(canonical_native,compact,mapping,pack)
        output_schema=compact_protocol.analysis_fragment_schema()
        candidate={"protocol_version":compact_protocol.CANDIDATE_PROTOCOL_VERSION,"output_schema":output_schema,
                   "output_schema_sha256":hashlib.sha256(output_schema.encode()).hexdigest(),
                   "instructions":compact_protocol.CANDIDATE_INSTRUCTIONS,"context_pack":pack,
                   "skill_receipts":[],"injected":injected,
                   "compact_context":compact,"compact_context_sha256":fragment_runtime.digest(compact),
                   "ordinal_map":mapping,"ordinal_map_sha256":fragment_runtime.digest(mapping),
                   "adapter_version":compact_protocol.VERSION}
        candidate_sha=fragment_runtime.digest(candidate)
        context_payload={"candidate":candidate,"candidate_sha256":candidate_sha}
        context_envelope={"artifact_type":"context_pack_artifact","schema_version":"2.0","run_id":"run",
                          "contract_sha256":"6"*64,"payload":context_payload,
                          "payload_sha256":fragment_runtime.digest(context_payload)}
        assignment={"fragment_id":fragment_id,"repository":"repo","worker_id":"analysis-worker","candidate_sha256":candidate_sha,
                    "context_pack_sha256":fragment_runtime.digest(pack),"status":"applied",
                    "obligation_ids":[oid],"skill_receipt_ids":[]}
        worker_att=signed_role_attestation("analysis-worker",native,{"COMPACT_CONTEXT.json":compact},f"ses_worker_{index}")
        worker_att["receipt"]["model_call_limit"]=1
        worker_att=resign_role_attestation(worker_att); worker_hash=fragment_runtime.digest(worker_att["receipt"])
        assignments.append(assignment); fragments.append(expanded); context_artifacts.append(context_envelope)
        native_outputs.append({"artifact_type":"compact_native_output","schema_version":"1.0",
                               "fragment_id":fragment_id,"raw_native":native,"canonical_native":canonical_native})
        adapter_receipts.append({"artifact_type":"compact_adapter_receipt","schema_version":"1.0",
            "fragment_id":fragment_id,"raw_native_output_sha256":fragment_runtime.digest(native),
            "canonical_native_output_sha256":fragment_runtime.digest(canonical_native),
            "adapter_version":compact_protocol.VERSION,"ordinal_map_sha256":fragment_runtime.digest(mapping),
            "expanded_fragment_sha256":fragment_runtime.digest(expanded),"execution_receipt_sha256":worker_hash})
        telemetry.append({"artifact_type":"runner_telemetry","schema_version":"1.0","run_id":"run","fragment_id":fragment_id,
            "model":"deepseek/deepseek-v4-flash","candidate_sha256":candidate_sha,"fragment_sha256":fragment_runtime.digest(expanded),
            "context_sha256":fragment_runtime.digest(context_envelope),"session_id":f"ses_worker_{index}","execution_receipt_sha256":worker_hash,
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
    final_ledger=copy.deepcopy(ledger);final_rows={row["obligation_id"]:row for row in final_ledger["obligations"]}
    for assignment,expanded in zip(assignments,fragments):
        dispositions={row["obligation_id"]:row for row in expanded["dispositions"]}
        for obligation_id in assignment["obligation_ids"]:
            row=final_rows[obligation_id];disposition=dispositions[obligation_id]
            row.update(status="complete",assigned_fragment_id=assignment["fragment_id"],
                       assigned_worker_id=expanded["worker_instance"],skill_receipt_ids=[],
                       evidence=[fact for fact in expanded["facts"] if fact["obligation_id"]==obligation_id],
                       disposition={key:value for key,value in disposition.items() if key!="obligation_id"},
                       unresolved=[item for item in expanded["unresolved"] if item["obligation_id"]==obligation_id])
    value={"run_id":"run","inventories":[inventory],"baseline_ledgers":[ledger],"ledgers":[final_ledger],"assignments":assignments,
            "fragments":fragments,"context_artifacts":context_artifacts,
            "native_outputs":native_outputs,"adapter_receipts":adapter_receipts,
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

def rebind_worker_native(value, mutate):
    native_envelope=value["native_outputs"][0];mutate(native_envelope["raw_native"])
    native_hash=fragment_runtime.digest(native_envelope["raw_native"]);fragment_id=native_envelope["fragment_id"]
    next(row for row in value["adapter_receipts"] if row["fragment_id"]==fragment_id)["raw_native_output_sha256"]=native_hash
    return rebind_worker(value,lambda receipt:receipt.update(output_payload_sha256=native_hash))

def rebind_context_candidate(value, mutate):
    envelope=value["context_artifacts"][0];candidate=envelope["payload"]["candidate"]
    mutate(candidate)
    envelope["payload"]["candidate_sha256"]=fragment_runtime.digest(candidate)
    envelope["payload_sha256"]=fragment_runtime.digest(envelope["payload"])
    fragment_id=candidate.get("context_pack",{}).get("fragment_id")
    assignment=next(row for row in value["assignments"] if row["fragment_id"]==fragment_id)
    assignment["candidate_sha256"]=envelope["payload"]["candidate_sha256"]
    assignment["context_pack_sha256"]=fragment_runtime.digest(candidate.get("context_pack"))
    telemetry=next(row for row in value["telemetry"] if row["fragment_id"]==fragment_id)
    telemetry["candidate_sha256"]=assignment["candidate_sha256"]
    telemetry["context_sha256"]=fragment_runtime.digest(envelope)
    adapter=next(row for row in value["adapter_receipts"] if row["fragment_id"]==fragment_id)
    adapter["ordinal_map_sha256"]=fragment_runtime.digest(candidate.get("ordinal_map"))
    return rebind_worker(value,lambda receipt:receipt["artifact_bindings"][0].update(
        payload_sha256=fragment_runtime.digest(candidate.get("compact_context")),
    ))

def semantic_native(value, *, version=1, rows=None):
    assessments=sorted(value["semantic_assessments"],key=lambda row:row["claim_id"])
    decisions=[[ordinal,row["supported"],row["reason"]] for ordinal,row in enumerate(assessments)]
    return {"v":version,"a":decisions if rows is None else rows}

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

    def test_direct_judge_rejects_rebound_compact_mapping_selection_drift(self):
        def synchronize_path_drift(candidate):
            candidate["ordinal_map"]["items"][0]["path"]="different.c"
            candidate["ordinal_map_sha256"]=fragment_runtime.digest(candidate["ordinal_map"])
            candidate["compact_context"]["s"][0][1]="different.c"
            candidate["compact_context_sha256"]=fragment_runtime.digest(candidate["compact_context"])
            candidate["context_pack"]["allowed_ranges"][0]["path"]="different.c"
            candidate["injected"]["sources"][0]["path"]="different.c"

        def synchronize_line_drift(candidate):
            candidate["ordinal_map"]["items"][0]["line_end"]=2
            candidate["ordinal_map_sha256"]=fragment_runtime.digest(candidate["ordinal_map"])
            candidate["compact_context"]["s"][0][3]=2
            candidate["compact_context"]["s"][0][4]="bounded source line\nsecond line"
            candidate["compact_context_sha256"]=fragment_runtime.digest(candidate["compact_context"])
            candidate["context_pack"]["allowed_ranges"][0]["line_end"]=2
            source=candidate["injected"]["sources"][0]
            source["line_end"]=2;source["text"]="bounded source line\nsecond line"
            source["sha256"]=hashlib.sha256(source["text"].encode()).hexdigest()

        def mutate_compact(candidate, mutate):
            mutate(candidate["compact_context"])
            candidate["compact_context_sha256"]=fragment_runtime.digest(candidate["compact_context"])

        mutations=(
            ("synchronized-path",synchronize_path_drift),
            ("synchronized-lines",synchronize_line_drift),
            ("source-mapping",lambda candidate:mutate_compact(
                candidate,lambda compact:compact["s"][0].__setitem__(1,"different.c"))),
            ("action-text",lambda candidate:mutate_compact(
                candidate,lambda compact:compact["i"][0][1][0].__setitem__(1,"different action"))),
            ("action-missing",lambda candidate:mutate_compact(
                candidate,lambda compact:compact["i"][0].__setitem__(1,[]))),
            ("action-extra",lambda candidate:mutate_compact(
                candidate,lambda compact:compact["i"][0][1].append([99,"extra action"]))),
            ("fragment-id",lambda candidate:mutate_compact(
                candidate,lambda compact:compact.__setitem__("f","frag-rebound"))),
            ("range-coalescing",lambda candidate:candidate["context_pack"]["allowed_ranges"][0].update(
                line_end=2)),
        )
        for name,mutate in mutations:
            with self.subTest(drift=name):
                value=rebind_context_candidate(inputs(),mutate)
                with self.assertRaisesRegex(ValueError,"candidate"):
                    coverage_judge.judge_r2(value)

    def test_direct_judge_rejects_rebound_context_pack_authority_drift(self):
        mutations=(
            ("extra",lambda pack:pack.update(extra=True)),
            ("missing",lambda pack:pack.pop("worker")),
            ("type",lambda pack:pack.update(input_budget_tokens=True)),
            ("fixed-value",lambda pack:pack.update(artifact_type="other")),
            ("snapshot-binding",lambda pack:pack.update(snapshot_sha256="0"*64)),
            ("inventory-binding",lambda pack:pack.update(inventory_sha256="0"*64)),
            ("ledger-binding",lambda pack:pack.update(ledger_sha256="0"*64)),
            ("content-digests",lambda pack:pack["content_digests"].update(sources=[])),
            ("output-budget",lambda pack:pack.update(output_budget_tokens=4095)),
            ("budget-receipt",lambda pack:pack["budget_receipt"].update(estimator_version="other")),
        )
        for name,mutate in mutations:
            with self.subTest(pack_drift=name):
                value=rebind_context_candidate(inputs(),lambda candidate,mutate=mutate:mutate(
                    candidate["context_pack"]))
                with self.assertRaisesRegex(ValueError,"context pack"):
                    coverage_judge.judge_r2(value)

    def test_direct_judge_closes_baseline_and_final_ledger_collections(self):
        reordered=inputs();reordered["baseline_ledgers"].reverse()
        self.assertEqual("PASS",coverage_judge.judge_r2(refresh(reordered))["verdict"])
        for name,mutate in (
            ("missing",lambda value:value["baseline_ledgers"].pop()),
            ("extra",lambda value:value["baseline_ledgers"].append({"repository":"extra"})),
            ("duplicate",lambda value:value["baseline_ledgers"].append(copy.deepcopy(value["baseline_ledgers"][0]))),
        ):
            value=inputs();mutate(value)
            with self.subTest(collection=name):
                with self.assertRaises(ValueError): coverage_judge.judge_r2(refresh(value))
        value=inputs();value["ledgers"][0]["obligations"][0]["action"]="different action"
        with self.assertRaisesRegex(ValueError,"immutable"): coverage_judge.judge_r2(refresh(value))
        value=inputs();value["ledgers"][0]["obligations"][0]["evidence"]=[]
        with self.assertRaisesRegex(ValueError,"semantic projection"): coverage_judge.judge_r2(refresh(value))
        value=rebind_context_candidate(inputs(),lambda candidate:candidate["context_pack"].update(
            ledger_sha256=fragment_runtime.digest(inputs()["ledgers"][0])))
        with self.assertRaisesRegex(ValueError,"context pack"): coverage_judge.judge_r2(value)

    def test_direct_judge_replays_complete_compact_worker_and_semantic_closures(self):
        self.assertEqual("PASS",coverage_judge.judge_r2(inputs())["verdict"])

        reordered=inputs();reordered["context_artifacts"].reverse()
        self.assertEqual("PASS",coverage_judge.judge_r2(refresh(reordered))["verdict"])
        for mutation in ("missing","extra","duplicate"):
            value=inputs()
            if mutation=="missing": value["context_artifacts"].pop()
            elif mutation=="duplicate": value["context_artifacts"].append(copy.deepcopy(value["context_artifacts"][0]))
            else:
                extra=copy.deepcopy(value["context_artifacts"][0]);candidate=extra["payload"]["candidate"]
                candidate["context_pack"]["fragment_id"]="frag-extra"
                extra["payload"]["candidate_sha256"]=fragment_runtime.digest(candidate)
                extra["payload_sha256"]=fragment_runtime.digest(extra["payload"])
                value["context_artifacts"].append(extra)
            with self.subTest(context_collection=mutation):
                with self.assertRaisesRegex(ValueError,"context"):
                    coverage_judge.judge_r2(refresh(value))

        for version in (True,False,1.0,"1",None):
            value=rebind_worker_native(inputs(),lambda native,version=version:native.update(v=version))
            with self.subTest(worker_native_version=version):
                with self.assertRaisesRegex(ValueError,"native replay"):
                    coverage_judge.judge_r2(value)
        for name,mutate in (
            ("extra-key",lambda native:native.update(extra=True)),
            ("missing-claims",lambda native:native.pop("c")),
            ("wrong-items",lambda native:native.update(i={})),
        ):
            value=rebind_worker_native(inputs(),mutate)
            with self.subTest(worker_native_schema=name):
                with self.assertRaisesRegex(ValueError,"native replay"):
                    coverage_judge.judge_r2(value)

        for version in (True,False,1.0,"1",None):
            def mutate_version(candidate,version=version):
                candidate["compact_context"]["v"]=version
                candidate["compact_context_sha256"]=fragment_runtime.digest(candidate["compact_context"])
            value=rebind_context_candidate(inputs(),mutate_version)
            with self.subTest(context_version=version):
                with self.assertRaisesRegex(ValueError,"candidate"):
                    coverage_judge.judge_r2(value)

        def changed_schema(candidate):
            candidate["output_schema"]="{}"
            candidate["output_schema_sha256"]=hashlib.sha256(candidate["output_schema"].encode()).hexdigest()
        def changed_source(candidate):
            source=candidate["injected"]["sources"][0];source["text"]="different source"
            source["sha256"]=hashlib.sha256(source["text"].encode()).hexdigest()
        def changed_skill(candidate):
            candidate["injected"]["skills"].append({"receipt_id":"SR-"+"9"*16,"skill_id":"storage-spdk",
                "version":"sha256:"+"8"*64,"content_sha256":"7"*64,"sha256":"7"*64,"text":"different skill"})
        def changed_compact(candidate):
            candidate["compact_context"]["q"]["claim_limit"]=2
            candidate["compact_context_sha256"]=fragment_runtime.digest(candidate["compact_context"])
        def changed_ordinal(candidate):
            candidate["ordinal_map"]=copy.deepcopy(candidate["ordinal_map"])
            candidate["ordinal_map"]["items"].reverse()
            candidate["ordinal_map_sha256"]=fragment_runtime.digest(candidate["ordinal_map"])
        mutations=(
            ("instructions",lambda candidate:candidate.update(instructions="synchronized drift")),
            ("output-schema-bytes-and-hash",changed_schema),
            ("output-schema-hash-only",lambda candidate:candidate.update(output_schema_sha256="0"*64)),
            ("injected-source",changed_source),("injected-skill",changed_skill),
            ("candidate-extra-key",lambda candidate:candidate.update(extra=True)),
            ("candidate-missing-key",lambda candidate:candidate.pop("instructions")),
            ("protocol",lambda candidate:candidate.update(protocol_version="3.0")),
            ("adapter",lambda candidate:candidate.update(adapter_version="other")),
            ("compact-projection",changed_compact),
            ("compact-hash-only",lambda candidate:candidate.update(compact_context_sha256="0"*64)),
            ("ordinal-projection",changed_ordinal),
            ("ordinal-hash-only",lambda candidate:candidate.update(ordinal_map_sha256="0"*64)),
        )
        for name,mutate in mutations:
            value=rebind_context_candidate(inputs(),mutate)
            with self.subTest(candidate_drift=name):
                with self.assertRaisesRegex(ValueError,"candidate"):
                    coverage_judge.judge_r2(value)

        for field,bad in (
            ("artifact_type","wrong"),("schema_version","2.0"),("adapter_version","wrong"),
            ("ordinal_map_sha256","0"*64),("expanded_fragment_sha256","0"*64),
            ("raw_native_output_sha256","0"*64),("canonical_native_output_sha256","0"*64),
            ("execution_receipt_sha256","0"*64),
        ):
            value=inputs();value["adapter_receipts"][0][field]=bad
            with self.subTest(adapter_field=field):
                with self.assertRaisesRegex(ValueError,"adapter projection"):
                    coverage_judge.judge_r2(refresh(value))
        value=inputs();value["adapter_receipts"][0]["extra"]=True
        with self.assertRaisesRegex(ValueError,"adapter projection"):
            coverage_judge.judge_r2(refresh(value))

        for version in (True,False,1.0,"1",None):
            value=inputs();native=semantic_native(value,version=version)
            value=rebind_auditor(value,lambda receipt,native=native:
                                 receipt.update(output_payload_sha256=fragment_runtime.digest(native)))
            with self.subTest(semantic_native_version=version):
                with self.assertRaisesRegex(ValueError,"semantic batch replay"):
                    coverage_judge.judge_r2(value)
        value=inputs();native=semantic_native(value);native["a"].reverse()
        value=rebind_auditor(value,lambda receipt:receipt.update(output_payload_sha256=fragment_runtime.digest(native)))
        with self.assertRaisesRegex(ValueError,"semantic batch replay"):
            coverage_judge.judge_r2(value)
        for reason in ("1234567","界"*11):
            value=inputs();value["semantic_assessments"][0]["reason"]=reason;native=semantic_native(value)
            value=rebind_auditor(value,lambda receipt,native=native:
                                 receipt.update(output_payload_sha256=fragment_runtime.digest(native)))
            with self.subTest(semantic_reason_bytes=len(reason.encode("utf-8"))):
                with self.assertRaisesRegex(ValueError,"semantic batch replay"):
                    coverage_judge.judge_r2(value)
        value=inputs();value["semantic_assessments"][0]["supported"]="true";native=semantic_native(value)
        value=rebind_auditor(value,lambda receipt:receipt.update(output_payload_sha256=fragment_runtime.digest(native)))
        with self.assertRaisesRegex(ValueError,"semantic batch replay"):
            coverage_judge.judge_r2(value)

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
        for field in ("raw_native_output_sha256","canonical_native_output_sha256",
                      "expanded_fragment_sha256","execution_receipt_sha256"):
            value=inputs(); value["adapter_receipts"][0][field]="0"*64
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError,"adapter projection"):
                    coverage_judge.judge_r2(refresh(value))
        value=inputs();canonical=value["native_outputs"][0]["canonical_native"]
        canonical["a"][0][2]="different result"
        value["adapter_receipts"][0]["canonical_native_output_sha256"]=fragment_runtime.digest(canonical)
        with self.assertRaisesRegex(ValueError,"expanded fragment replay"):
            coverage_judge.judge_r2(refresh(value))

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
        with self.assertRaisesRegex(ValueError,"semantic batch replay"):
            coverage_judge.judge_r2(reordered)

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
