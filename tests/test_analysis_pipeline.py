from __future__ import annotations

import copy, hashlib, json, os, subprocess, tempfile, unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runtime import analysis_pipeline, runctl, data_runtime
from evaluation import benchmark, composer
from tests.test_contract_lifecycle import ContractLifecycleTests
from tests.test_analysis_depth_contract import AnalysisDepthContractTests
from tests.test_analysis_report_projection import AnalysisReportProjectionTests
from tests.test_evaluation_benchmark import _native_stream, _debug_config, _sequence_runner
from unittest.mock import Mock


class AnalysisPipelineTests(unittest.TestCase):
    def fixture(self, large=False, multi=False, huge=False):
        holder = tempfile.TemporaryDirectory(); root = Path(holder.name); helper = ContractLifecycleTests()
        helper.prepare(root)
        if large or huge:
            repo=root/"pangea-data/repositories/driver"
            count=80 if huge else 10
            (repo/"driver.c").write_text("int nvme_cli_format_admin(void) { return 0; }\n"+"\n".join(f"int f{i}(int x){{ if(x) return {i}; return 0; }}" for i in range(count))+"\n")
            subprocess.run(["git","-C",str(repo),"add","driver.c"],check=True)
            subprocess.run(["git","-C",str(repo),"-c","user.email=test@example.invalid","-c","user.name=PANGEA Test","commit","--quiet","-m","large"],check=True)
        else:
            repo=root/"pangea-data/repositories/driver"
            (repo/"driver.c").write_text("int nvme_cli_format_admin(void) { return 0; }\n")
            subprocess.run(["git","-C",str(repo),"add","driver.c"],check=True)
            subprocess.run(["git","-C",str(repo),"-c","user.email=test@example.invalid","-c","user.name=PANGEA Test","commit","--quiet","-m","storage-trigger"],check=True)
        repo_args=["--repository","driver","--source-scope","driver=driver.c"]
        if multi:
            second=root/"pangea-data/repositories/second"; second.mkdir(); subprocess.run(["git","init","--quiet",str(second)],check=True)
            (second/"second.c").write_text("int second(void) { return 2; }\n")
            subprocess.run(["git","-C",str(second),"add","second.c"],check=True)
            subprocess.run(["git","-C",str(second),"-c","user.email=test@example.invalid","-c","user.name=PANGEA Test","commit","--quiet","-m","initial"],check=True)
            receipt=root/"pangea-data/session/preflight-receipt.json"; value=json.loads(receipt.read_text()); value["known_repositories"].append("second"); receipt.write_text(json.dumps(value))
            repo_args += ["--repository","second","--source-scope","second=second.c"]
        helper.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "driver", *repo_args,
                   "--analysis-depth", "complete", "--contract-id", "r2")
        helper.cli(root, "confirm-contract-v2", "--contract-id", "r2", "--revision", "1",
                   "--source", "user_reply", "--materials-status", "confirmed_none")
        activated = helper.cli(root, "activate-contract-v2", "--contract-id", "r2", "--run-id", "r2-run")
        return holder, root, Path(activated["run_dir"])

    def fragment(self, run: Path, index=0) -> Path:
        assignment_index = json.loads((run/"internal/assignment-index.json").read_text())
        assignment = assignment_index["payload"]["assignments"][index]
        context = json.loads((run/f"internal/context-packs/{assignment['fragment_id']}/CONTEXT.json").read_text())
        candidate = context["payload"]["candidate"]
        repository = assignment["repository"]
        pack = candidate["context_pack"]; inv = json.loads((run/f"internal/inventories/{repository}.json").read_text())["payload"]
        ledger = json.loads((run/f"internal/baseline-ledgers/{repository}.json").read_text())["payload"]
        manifest_run_id = json.loads((run/"manifest.json").read_text())["run_id"]
        assert assignment_index["run_id"] == context["run_id"] == pack["run_id"] == manifest_run_id
        items={x["inventory_id"]:x for x in inv["items"]}; rows={x["obligation_id"]:x for x in ledger["obligations"]}; sources={tuple(x["inventory_ids"]):x for x in candidate["injected"]["sources"]}
        facts=[]
        for oid in assignment["obligation_ids"]:
            item=items[rows[oid]["inventory_id"]]; src=next(x for ids,x in sources.items() if item["inventory_id"] in ids)
            lines=src["text"].splitlines() or [""]; lo=item["line_start"]-src["line_start"]; hi=item["line_end"]-src["line_start"]+1
            excerpt="\n".join(lines[lo:hi])
            facts.append({"obligation_id":oid,"inventory_id":item["inventory_id"],"path":item["path"],
                          "line_start":item["line_start"],"line_count":item["line_end"]-item["line_start"]+1,
                          "excerpt_sha256":hashlib.sha256(excerpt.encode()).hexdigest(),"evidence":"bounded source"})
        contributions={name:[] for name in analysis_pipeline.fragment_runtime.CONTRIBUTION_FAMILIES}
        first=facts[0]; key=[first["obligation_id"],first["inventory_id"],first["line_start"],first["line_count"]]
        for family in analysis_pipeline.fragment_runtime.CONTRIBUTION_FAMILIES:
            cp={"priority":"P2","obligation_id":first["obligation_id"],"fact_keys":[key],"summary":f"bounded {family} source analysis","controls":[],"oracles":[]}
            contributions[family]=[{"contribution_id":analysis_pipeline.fragment_runtime._canonical_id("C-",cp),**cp}]
        rp={"severity":"High","obligation_id":first["obligation_id"],"fact_keys":[key],"summary":"bounded state may remain failed",
            "trigger":"invalid bounded request","propagation":"error retains state","impact":"next operation fails",
            "observation":"return code and state log","recovery":"valid request restores state",
            "control":"send invalid then valid request","oracle":"first fails and second succeeds"}
        risks=[{"risk_id":analysis_pipeline.fragment_runtime._canonical_id("R-",rp),**rp}]
        frag={"artifact_type":"analysis_fragment","schema_version":"2.0","worker_instance":"analysis-worker",
              "run_id":pack["run_id"],"fragment_id":assignment["fragment_id"],"context_pack_sha256":analysis_pipeline._digest(pack),
              "obligation_ids":assignment["obligation_ids"],"skill_receipt_ids":assignment["skill_receipt_ids"],
              "facts":facts,"contributions":contributions,"risk_cards":risks,
              "dispositions":[{"obligation_id":o,"outcome":"analyzed","reason":"bounded source inspected"} for o in assignment["obligation_ids"]],
              "unresolved":[],"usage":{"output_tokens":64,"finish_reason":"stop","valid_json":True}}
        path=run/f"tmp/worker-output-{index}.json"; path.write_text(json.dumps(frag)); return path

    def signed_worker_execution(self,run:Path,fragment_path:Path):
        fragment=json.loads(fragment_path.read_text()); fragment=fragment.get("payload",fragment)
        context_path=run/f"internal/context-packs/{fragment['fragment_id']}/CONTEXT.json"; context=json.loads(context_path.read_text())
        stream=_native_stream(text=json.dumps(fragment)); runner=_sequence_runner([Mock(returncode=0,stdout="1.18.4\n",stderr=""),
            Mock(returncode=0,stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["analysis-worker"],name="analysis-worker",mode="subagent",safe_overlay=True),stderr=""),
            Mock(returncode=0,stdout=stream,stderr="")])
        execution=benchmark.execute_isolated_role("analysis-worker",{"CONTEXT.json":context},run=runner,
            environ={"PATH":"/bin","DEEPSEEK_API_KEY":"test-provider-value"},scratch_parent=run/"tmp")
        return context_path,execution

    def test_real_run_build_issue_is_idempotent_and_candidate_is_unique(self):
        holder,root,run=self.fixture()
        try:
            first=analysis_pipeline.build_denominator(root,"r2-run"); self.assertGreater(first["obligations"],0)
            self.assertTrue(analysis_pipeline.build_denominator(root,"r2-run")["recovered"])
            self.assertEqual(1,analysis_pipeline.issue_context(root,"r2-run")["assignments"])
            self.assertEqual(1,analysis_pipeline.issue_context(root,"r2-run")["assignments"])
            payload=json.loads(next((run/"internal/context-packs").glob("*/CONTEXT.json")).read_text())["payload"]
            self.assertEqual({"candidate","candidate_sha256"},set(payload))
            self.assertEqual(analysis_pipeline._digest(payload["candidate"]),payload["candidate_sha256"])
            self.assertEqual((Path(__file__).parents[1]/"schemas/analysis-fragment.schema.json").read_text(),payload["candidate"]["output_schema"])
            snapshot=run/"tmp/snapshots/driver"
            for source in payload["candidate"]["injected"]["sources"]:
                lines=(snapshot/source["path"]).read_text().splitlines() or [""]
                expected="\n".join(lines[source["line_start"]-1:source["line_end"]])
                self.assertEqual(expected,source["text"]); self.assertEqual(hashlib.sha256(expected.encode()).hexdigest(),source["sha256"])
            self.assertIn("## references/",payload["candidate"]["injected"]["skills"][0]["text"] if payload["candidate"]["injected"]["skills"] else "## references/")
        finally: holder.cleanup()

    def test_apply_fault_recovery_and_exact_replay(self):
        for point in ("prepared","ledger_published","assignment_published","obligation_published"):
            with self.subTest(point=point):
                holder,root,run=self.fixture()
                try:
                    analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run)
                    os.environ["PANGEA_PIPELINE_FAULT"]=point
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
                    os.environ.pop("PANGEA_PIPELINE_FAULT")
                    self.assertTrue(analysis_pipeline.apply_fragment(root,"r2-run",frag)["applied"])
                    self.assertTrue(analysis_pipeline.apply_fragment(root,"r2-run",frag)["recovered"])
                    changed=json.loads(frag.read_text()); changed["usage"]["output_tokens"]+=1; frag.write_text(json.dumps(changed))
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
                finally:
                    os.environ.pop("PANGEA_PIPELINE_FAULT",None); holder.cleanup()

    def test_build_and_issue_publication_faults_are_not_executable(self):
        holder,root,run=self.fixture()
        try:
            os.environ["PANGEA_PIPELINE_FAULT"]="build:inventory-index.json"
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.build_denominator(root,"r2-run")
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.issue_context(root,"r2-run")
            os.environ.pop("PANGEA_PIPELINE_FAULT"); analysis_pipeline.build_denominator(root,"r2-run")
            os.environ["PANGEA_PIPELINE_FAULT"]="issue:planned"
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.issue_context(root,"r2-run")
            self.assertEqual([],json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"])
            os.environ.pop("PANGEA_PIPELINE_FAULT"); self.assertEqual(1,analysis_pipeline.issue_context(root,"r2-run")["assignments"])
        finally:
            os.environ.pop("PANGEA_PIPELINE_FAULT",None); holder.cleanup()

    def test_greedy_shrink_rebuilds_exact_nonoverlapping_batches(self):
        holder,root,run=self.fixture(large=True); original=analysis_pipeline.context_budget.build
        try:
            analysis_pipeline.build_denominator(root,"r2-run")
            def capped(*args,**kwargs):
                if len(args[3])>3: raise analysis_pipeline.context_budget.ContextError("test cap")
                return original(*args,**kwargs)
            analysis_pipeline.context_budget.build=capped
            count=analysis_pipeline.issue_context(root,"r2-run")["assignments"]
            self.assertGreater(count,1)
            assignments=json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"]
            flattened=[o for a in assignments for o in a["obligation_ids"]]
            self.assertEqual(len(flattened),len(set(flattened)))
            for a in assignments:
                candidate=json.loads((run/f"internal/context-packs/{a['fragment_id']}/CONTEXT.json").read_text())["payload"]["candidate"]
                self.assertEqual(set(a["obligation_ids"]),set(candidate["context_pack"]["obligation_ids"]))
                for receipt in candidate["skill_receipts"]: self.assertLessEqual(set(receipt["obligation_ids"]),set(a["obligation_ids"]))
            self.assertEqual(count,analysis_pipeline.issue_context(root,"r2-run")["assignments"])
        finally:
            analysis_pipeline.context_budget.build=original; holder.cleanup()

    def test_multi_repo_denominator_and_assignments(self):
        holder,root,run=self.fixture(multi=True)
        try:
            self.assertEqual(2,analysis_pipeline.build_denominator(root,"r2-run")["repositories"])
            analysis_pipeline.issue_context(root,"r2-run")
            assignments=json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"]
            self.assertEqual({"driver","second"},{a["repository"] for a in assignments})
        finally: holder.cleanup()

    def test_budget_failures_publish_zero_executable_contexts(self):
        for large in (False,True):
            holder,root,run=self.fixture(huge=large); original=analysis_pipeline.context_budget.build
            try:
                analysis_pipeline.build_denominator(root,"r2-run")
                def reject(*args,**kwargs): raise analysis_pipeline.context_budget.ContextError("forced overbudget")
                if not large: analysis_pipeline.context_budget.build=reject
                else:
                    def one(*args,**kwargs):
                        if len(args[3])>1: raise analysis_pipeline.context_budget.ContextError("forced split")
                        return original(*args,**kwargs)
                    analysis_pipeline.context_budget.build=one
                with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.issue_context(root,"r2-run")
                self.assertEqual([],json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"])
                self.assertFalse((run/"internal/context-packs").exists())
            finally:
                analysis_pipeline.context_budget.build=original; holder.cleanup()

    def test_fragment_import_rejects_symlink_leaf_and_ancestor(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run)
            leaf=run/"tmp/leaf.json"; leaf.symlink_to(frag)
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",leaf)
            outside=Path(holder.name)/"outside"; outside.mkdir(); (outside/"f.json").write_text(frag.read_text())
            parent=run/"tmp/jump"; parent.symlink_to(outside,target_is_directory=True)
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",parent/"f.json")
        finally: holder.cleanup()

    def test_multi_batch_apply_forward_reverse_and_early_replay(self):
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                holder,root,run=self.fixture(large=True); original=analysis_pipeline.context_budget.build
                try:
                    analysis_pipeline.build_denominator(root,"r2-run")
                    def capped(*args,**kwargs):
                        if len(args[3])>3: raise analysis_pipeline.context_budget.ContextError("split")
                        return original(*args,**kwargs)
                    analysis_pipeline.context_budget.build=capped; count=analysis_pipeline.issue_context(root,"r2-run")["assignments"]
                    fragments=[self.fragment(run,i) for i in range(count)]; order=list(reversed(range(count))) if reverse else list(range(count))
                    for i in order: self.assertTrue(analysis_pipeline.apply_fragment(root,"r2-run",fragments[i])["applied"])
                    self.assertTrue(analysis_pipeline.apply_fragment(root,"r2-run",fragments[0])["recovered"])
                    ledger=json.loads((run/"internal/ledgers/driver.json").read_text())["payload"]
                    self.assertTrue(all(r["status"]=="complete" for r in ledger["obligations"]))
                finally:
                    analysis_pipeline.context_budget.build=original; holder.cleanup()

    def test_concurrent_apply_has_no_lost_update(self):
        holder,root,run=self.fixture(large=True); original=analysis_pipeline.context_budget.build
        try:
            analysis_pipeline.build_denominator(root,"r2-run")
            def capped(*args,**kwargs):
                if len(args[3])>3: raise analysis_pipeline.context_budget.ContextError("split")
                return original(*args,**kwargs)
            analysis_pipeline.context_budget.build=capped; count=analysis_pipeline.issue_context(root,"r2-run")["assignments"]
            fragments=[self.fragment(run,i) for i in range(count)]
            with ThreadPoolExecutor(max_workers=min(8,count)) as pool:
                results=list(pool.map(lambda p:analysis_pipeline.apply_fragment(root,"r2-run",p),fragments))
            self.assertTrue(all(x["applied"] for x in results))
            ledger=json.loads((run/"internal/ledgers/driver.json").read_text())["payload"]
            self.assertTrue(all(r["status"]=="complete" for r in ledger["obligations"]))
        finally:
            analysis_pipeline.context_budget.build=original; holder.cleanup()

    def test_scope_confirmation_and_import_boundary_fail_closed(self):
        holder,root,run=self.fixture()
        try:
            contract=json.loads((run/"internal/task-contract.json").read_text()); contract["source_scopes_sha256"]="0"*64
            (run/"internal/task-contract.json").write_text(json.dumps(contract))
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.build_denominator(root,"r2-run")
        finally: holder.cleanup()

    def test_schema_mutation_rejected(self):
        envelope={"artifact_type":"assignment_index","schema_version":"2.0","run_id":"r",
                  "contract_sha256":"0"*64,"payload":{"assignments":[],"extra":1},"payload_sha256":"0"*64}
        with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline._validate_artifact(envelope)

    def test_every_persisted_artifact_family_rejects_payload_mutation(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run); analysis_pipeline.apply_fragment(root,"r2-run",frag)
            paths=[run/"internal/source-bindings.json",run/"internal/inventory-index.json",run/"internal/obligation-index.json",
                   run/"internal/assignment-index.json",run/"internal/inventories/driver.json",run/"internal/ledgers/driver.json",
                   run/"internal/denominator-state.json",run/"internal/context-publication-state.json",
                   next((run/"internal/context-packs").glob("*/CONTEXT.json")),next((run/"internal/fragments").glob("*.json")),next((run/"internal/transactions").glob("*.json"))]
            for path in paths:
                with self.subTest(path=path.name):
                    env=json.loads(path.read_text()); bad=copy.deepcopy(env); bad["payload"]["unexpected"]=1
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline._validate_artifact(bad)
                    missing=copy.deepcopy(env); key=next(iter(missing["payload"])); missing["payload"].pop(key)
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline._validate_artifact(missing)
        finally: holder.cleanup()

    def test_published_artifacts_are_readonly_and_hash_checked(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run")
            path=run/"internal/inventory-index.json"; self.assertEqual(0,path.stat().st_mode & 0o222)
            value=json.loads(path.read_text()); value["payload_sha256"]="0"*64
            path.chmod(0o600); path.write_text(json.dumps(value)); path.chmod(0o400)
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline._read(path)
        finally: holder.cleanup()

    def test_real_r2_fragment_native_telemetry_semantic_receipt_to_stage_report(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag_path=self.fragment(run)
            context_path,worker_execution=self.signed_worker_execution(run,frag_path)
            signed_path=benchmark.write_isolated_worker_fragment(run,context_path,worker_execution)
            analysis_pipeline.apply_fragment(root,"r2-run",signed_path)
            assignment=json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"][0]
            managed=run/f"internal/fragments/{assignment['fragment_id']}.json"
            benchmark.write_native_runner_telemetry(run,managed,context_path,worker_execution)
            fragment=json.loads(managed.read_text())["payload"]
            claims=[item for family in analysis_pipeline.fragment_runtime.CONTRIBUTION_FAMILIES for item in fragment["contributions"][family]]+fragment["risk_cards"]
            for claim in claims:
                stream=_native_stream(text=json.dumps({"supported":True,"reason":"auditor confirmed exact excerpt support"}))
                runner=_sequence_runner([Mock(returncode=0,stdout="1.18.4\n",stderr=""),
                    Mock(returncode=0,stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["auditor"],name="auditor",mode="subagent",safe_overlay=True),stderr=""),
                    Mock(returncode=0,stdout=stream,stderr="")])
                execution=benchmark.execute_isolated_role("auditor",{"CLAIM.json":claim,"FACTS.json":fragment["facts"]},
                    run=runner,environ={"PATH":"/bin","DEEPSEEK_API_KEY":"test-provider-value"},scratch_parent=run/"tmp")
                benchmark.write_native_semantic_assessment(run,claim,fragment["facts"],execution)
            AnalysisDepthContractTests.complete_checkpoints(root,"r2-run")
            model=AnalysisDepthContractTests.model(run); model["r2_projection"]=runctl._expected_r2_projection(run)
            hc_id=fragment["risk_cards"][0]["risk_id"]
            model["test_scenarios"][0]["risk_ids"].append(hc_id); model["test_cases"][0]["risk_ids"].append(hc_id)
            model_path=root/"r2-model.json"; model_path.write_text(json.dumps(model,ensure_ascii=False))
            helper=ContractLifecycleTests(); helper.cli(root,"stage-analysis-v2","--run-id","r2-run","--file",str(model_path))
            low=AnalysisReportProjectionTests.risk(); low["severity"]="Low"; data_runtime.upsert_risk(root,"r2-run",low)
            fragment_risk=fragment["risk_cards"][0]
            high=copy.deepcopy(low); high.update({
                "risk_id":fragment_risk["risk_id"], "title":fragment_risk["summary"],
                "severity":fragment_risk["severity"], "trigger":fragment_risk["trigger"],
                "propagation":fragment_risk["propagation"], "external_impact":fragment_risk["impact"],
                "observation":fragment_risk["observation"], "recovery":fragment_risk["recovery"],
                "test_explanation":f"Control: {fragment_risk['control']}\nOracle: {fragment_risk['oracle']}",
                "evidence":[{"location":runctl._r2_fact_location(fragment["facts"][0]),
                             "observation":fragment["facts"][0]["evidence"]}],
            }); data_runtime.upsert_risk(root,"r2-run",high)
            contract=json.loads((run/"internal/task-contract.json").read_text()); draft={"title":"R2 report","task_contract":contract,
                "code_map":[{}],"flows":[{}],"branches":[{}],"risks":[low,high],"scenarios":[],"test_cases":[],"unresolved":[],"next_steps":[]}
            draft_path=root/"r2-report.json"; draft_path.write_text(json.dumps(draft,ensure_ascii=False))
            staged=helper.cli(root,"stage-report-v2","--run-id","r2-run","--file",str(draft_path))
            judge=json.loads((run/"internal/coverage-judge.json").read_text()); self.assertEqual("PASS",judge["verdict"])
            verified, fixed_hashes = composer._fixed_judge_closure(run, "r2-run")
            self.assertEqual(judge, verified); self.assertEqual(64, len(fixed_hashes["coverage_judge"]))
            changed_judge = copy.deepcopy(judge); changed_judge["verdict"] = "FAIL"
            judge_path = run / "internal/coverage-judge.json"
            judge_path.chmod(0o600); judge_path.write_text(json.dumps(changed_judge)); judge_path.chmod(0o400)
            with self.assertRaisesRegex(composer.ComposerError, "deterministic recomputation"):
                composer._fixed_judge_closure(run, "r2-run")
            data_runtime.atomic_write_json(judge_path, judge)
            projection=model["r2_projection"]
            self.assertTrue(projection["hc_risks"]["ids"])
            self.assertTrue(all(projection["contributions"][family]["ids"] for family in analysis_pipeline.fragment_runtime.CONTRIBUTION_FAMILIES))
            self.assertTrue(judge["input_artifacts"]); self.assertEqual(str(run/"internal/coverage-judge.json"),staged["coverage_judge"])
            report=json.loads((run/"internal/report-model.json").read_text()); ledger_path=run/"internal/risk-ledger.json"
            original_ledger=json.loads(ledger_path.read_text())
            for field in ("title","trigger","propagation","external_impact","observation","recovery","test_explanation",
                          "evidence","extra_evidence","duplicate_evidence"):
                with self.subTest(field=field):
                    changed_report=copy.deepcopy(report); changed_ledger=copy.deepcopy(original_ledger)
                    report_risk=next(x for x in changed_report["risks"] if x["risk_id"]==fragment_risk["risk_id"])
                    ledger_risk=next(x for x in changed_ledger["risks"] if x["risk_id"]==fragment_risk["risk_id"])
                    if field=="evidence": report_risk[field]=ledger_risk[field]=[{"location":"driver.c:999","observation":"different"}]
                    elif field=="extra_evidence":
                        extra={"location":"driver.c:999","observation":"different"}
                        report_risk["evidence"].append(extra); ledger_risk["evidence"].append(copy.deepcopy(extra))
                    elif field=="duplicate_evidence":
                        report_risk["evidence"].append(copy.deepcopy(report_risk["evidence"][0]))
                        ledger_risk["evidence"].append(copy.deepcopy(ledger_risk["evidence"][0]))
                    else: report_risk[field]=ledger_risk[field]="different but same risk id"
                    data_runtime.atomic_write_json(ledger_path,changed_ledger)
                    with self.assertRaises(runctl.RunCtlError): runctl._assert_r2_hc_risk_binding(run,changed_report)
                    data_runtime.atomic_write_json(ledger_path,original_ledger)
        finally: holder.cleanup()

    def test_pre_judge_replay_rejects_publication_transaction_and_fragment_drift(self):
        cases=(
            ("denominator",lambda run:run/"internal/denominator-state.json",lambda env:env["payload"]["artifacts"][0].update(sha256="0"*64)),
            ("context",lambda run:run/"internal/context-publication-state.json",lambda env:env["payload"]["contexts"][0].update(sha256="0"*64)),
            ("transaction",lambda run:next((run/"internal/transactions").glob("*.json")),lambda env:env["payload"].update(state="prepared")),
            ("fragment-body",lambda run:next((run/"internal/fragments").glob("*.json")),lambda env:env["payload"]["contributions"]["flows"][0].update(summary="changed body with retained id")),
        )
        for name,locate,mutate in cases:
            with self.subTest(name=name):
                holder,root,run=self.fixture()
                try:
                    analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); fragment_path=self.fragment(run)
                    context_path,worker_execution=self.signed_worker_execution(run,fragment_path)
                    signed_path=benchmark.write_isolated_worker_fragment(run,context_path,worker_execution)
                    analysis_pipeline.apply_fragment(root,"r2-run",signed_path)
                    assignment=json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"][0]
                    benchmark.write_native_runner_telemetry(run,run/f"internal/fragments/{assignment['fragment_id']}.json",context_path,worker_execution)
                    self.assertEqual("verified",analysis_pipeline.validate_run_for_judge(root,"r2-run")["status"])
                    path=locate(run); env=json.loads(path.read_text()); mutate(env); env["payload_sha256"]=analysis_pipeline._digest(env["payload"])
                    path.chmod(0o600); path.write_text(json.dumps(env)); path.chmod(0o400)
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.validate_run_for_judge(root,"r2-run")
                finally: holder.cleanup()

    def test_pre_judge_rejects_self_declared_worker_execution_receipt(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run")
            fragment_path=self.fragment(run); analysis_pipeline.apply_fragment(root,"r2-run",fragment_path)
            assignment=json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"][0]
            fragment=json.loads((run/f"internal/fragments/{assignment['fragment_id']}.json").read_text())["payload"]
            context=json.loads((run/f"internal/context-packs/{assignment['fragment_id']}/CONTEXT.json").read_text())
            telemetry={"artifact_type":"runner_telemetry","schema_version":"1.0","run_id":"r2-run",
                "fragment_id":assignment["fragment_id"],"model":"deepseek/deepseek-v4-flash",
                "candidate_sha256":assignment["candidate_sha256"],"fragment_sha256":analysis_pipeline._digest(fragment),
                "context_sha256":analysis_pipeline._digest(context),"session_id":"self-declared-session",
                "execution_receipt_sha256":"f"*64,"input_tokens":100,"output_tokens":20,"finish_reason":"stop",
                "valid_json":True,"captured_by":"opencode-runner"}
            path=run/f"internal/telemetry/{assignment['fragment_id']}.json"; path.parent.mkdir(); path.write_text(json.dumps(telemetry))
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.validate_run_for_judge(root,"r2-run")
        finally: holder.cleanup()

    def test_pre_judge_requires_exact_execution_receipt_directory_members(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run")
            candidate=self.fragment(run); context_path,execution=self.signed_worker_execution(run,candidate)
            imported=benchmark.write_isolated_worker_fragment(run,context_path,execution)
            analysis_pipeline.apply_fragment(root,"r2-run",imported)
            assignment=json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"][0]
            managed=run/f"internal/fragments/{assignment['fragment_id']}.json"
            benchmark.write_native_runner_telemetry(run,managed,context_path,execution)
            self.assertEqual("verified",analysis_pipeline.validate_run_for_judge(root,"r2-run")["status"])
            receipt_dir=run/"internal/execution-receipts"
            cases=(("note.txt",lambda path:path.write_text("extra")),("extra.json",lambda path:path.write_text("{}")),("extra-dir",lambda path:path.mkdir()))
            for name,create in cases:
                with self.subTest(name=name):
                    path=receipt_dir/name; create(path)
                    with self.assertRaisesRegex(analysis_pipeline.PipelineError,"exact reference closure"):
                        analysis_pipeline.validate_run_for_judge(root,"r2-run")
                    path.rmdir() if path.is_dir() else path.unlink()
        finally: holder.cleanup()

    def test_pre_judge_requires_exact_members_in_every_r2_managed_collection(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run")
            candidate=self.fragment(run); context_path,execution=self.signed_worker_execution(run,candidate)
            imported=benchmark.write_isolated_worker_fragment(run,context_path,execution)
            analysis_pipeline.apply_fragment(root,"r2-run",imported)
            assignment=json.loads((run/"internal/assignment-index.json").read_text())["payload"]["assignments"][0]
            benchmark.write_native_runner_telemetry(run,run/f"internal/fragments/{assignment['fragment_id']}.json",context_path,execution)
            self.assertEqual("verified",analysis_pipeline.validate_run_for_judge(root,"r2-run")["status"])
            directories=[run/f"internal/{name}" for name in
                         ("fragments","transactions","telemetry","inventories","ledgers","baseline-ledgers","context-packs")]
            directories.append(context_path.parent)
            for directory in directories:
                with self.subTest(directory=directory.name):
                    extra=directory/"NOTE.txt"; extra.write_text("extra")
                    try:
                        with self.assertRaises(analysis_pipeline.PipelineError):
                            analysis_pipeline.validate_run_for_judge(root,"r2-run")
                    finally: extra.unlink()
        finally: holder.cleanup()

    def test_managed_read_rejects_extra_root_and_zero_payload_hash(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); path=run/"internal/inventory-index.json"
            good=json.loads(path.read_text())
            for mutate in (lambda x:x.update(extra=True),lambda x:x.update(payload_sha256="0"*64)):
                bad=copy.deepcopy(good); mutate(bad); path.chmod(0o600); path.write_text(json.dumps(bad)); path.chmod(0o400)
                with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline._read(path)
                path.chmod(0o600); path.write_text(json.dumps(good)); path.chmod(0o400)
        finally: holder.cleanup()

    def test_issue_assignment_effect_before_error_recovers_same_nonempty_publication(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run")
            os.environ["PANGEA_PIPELINE_FAULT"]="issue:assignment-index"
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.issue_context(root,"r2-run")
            before=json.loads((run/"internal/context-publication-state.json").read_text())["payload"]
            self.assertEqual("publishing",before["status"]); self.assertTrue(before["contexts"]); self.assertTrue(before["assignments"])
            context_hashes={x["fragment_id"]:x["sha256"] for x in before["contexts"]}
            os.environ.pop("PANGEA_PIPELINE_FAULT")
            analysis_pipeline.issue_context(root,"r2-run")
            after=json.loads((run/"internal/context-publication-state.json").read_text())["payload"]
            self.assertEqual("committed",after["status"]); self.assertEqual(context_hashes,{x["fragment_id"]:x["sha256"] for x in after["contexts"]})
            self.assertTrue(analysis_pipeline.apply_fragment(root,"r2-run",self.fragment(run))["applied"])
        finally:
            os.environ.pop("PANGEA_PIPELINE_FAULT",None); holder.cleanup()

    def test_transaction_rejects_every_tampered_old_new_hash_and_impossible_state(self):
        hash_fields=("old_ledger_sha256","new_ledger_sha256","old_assignment_index_sha256","new_assignment_index_sha256",
                     "old_obligation_index_sha256","new_obligation_index_sha256","old_selected_rows_sha256","new_selected_rows_sha256")
        for field in hash_fields+("state",):
            with self.subTest(field=field):
                holder,root,run=self.fixture()
                try:
                    analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run)
                    os.environ["PANGEA_PIPELINE_FAULT"]="prepared"
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
                    os.environ.pop("PANGEA_PIPELINE_FAULT")
                    path=next((run/"internal/transactions").glob("*.json")); env=json.loads(path.read_text()); path.chmod(0o600)
                    env["payload"][field]="committed" if field=="state" else "0"*64
                    env["payload_sha256"]=analysis_pipeline._digest(env["payload"]); path.write_text(json.dumps(env)); path.chmod(0o400)
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
                finally:
                    os.environ.pop("PANGEA_PIPELINE_FAULT",None); holder.cleanup()

    def test_apply_recovers_effect_before_error_at_each_file_boundary(self):
        for target in ("ledgers/driver.json","assignment-index.json","obligation-index.json"):
            with self.subTest(target=target):
                holder,root,run=self.fixture(); original=analysis_pipeline._write; fired=[False]
                try:
                    analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run)
                    def after_effect(path,value):
                        original(path,value)
                        if not fired[0] and str(path).endswith(target):
                            fired[0]=True; raise analysis_pipeline.PipelineError("effect-before-error")
                    analysis_pipeline._write=after_effect
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
                    analysis_pipeline._write=original
                    self.assertTrue(analysis_pipeline.apply_fragment(root,"r2-run",frag)["recovered"])
                finally:
                    analysis_pipeline._write=original; holder.cleanup()

    def test_transaction_identity_and_embedded_payload_fields_are_not_rebindable(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run)
            os.environ["PANGEA_PIPELINE_FAULT"]="prepared"
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
            os.environ.pop("PANGEA_PIPELINE_FAULT")
            path=next((run/"internal/transactions").glob("*.json")); original=json.loads(path.read_text())
            def embedded(env):
                env["payload"]["old_ledger"]["repository"]="forged"
                env["payload"]["old_ledger_sha256"]=analysis_pipeline._digest(env["payload"]["old_ledger"])
            mutations=(lambda x:x["payload"].update(transaction_id="txn-"+"1"*16),
                       lambda x:x["payload"].update(run_id="other-run"),
                       lambda x:x["payload"].update(fragment_id="frag-"+"1"*16),
                       lambda x:x["payload"].update(fragment_sha256="1"*64),
                       lambda x:x["payload"].update(repository="forged"),embedded)
            for mutate in mutations:
                bad=copy.deepcopy(original); mutate(bad); bad["payload_sha256"]=analysis_pipeline._digest(bad["payload"])
                path.chmod(0o600); path.write_text(json.dumps(bad)); path.chmod(0o400)
                with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
            path.chmod(0o600); path.write_text(json.dumps(original)); path.chmod(0o400)
            self.assertTrue(analysis_pipeline.apply_fragment(root,"r2-run",frag)["recovered"])
        finally:
            os.environ.pop("PANGEA_PIPELINE_FAULT",None); holder.cleanup()

    def test_denominator_manifest_is_complete_and_missing_member_is_not_recovered(self):
        for missing in ("internal/source-bindings.json","internal/ledgers/driver.json"):
            with self.subTest(missing=missing):
                holder,root,run=self.fixture()
                try:
                    analysis_pipeline.build_denominator(root,"r2-run")
                    state=json.loads((run/"internal/denominator-state.json").read_text())["payload"]
                    self.assertGreaterEqual(len(state["artifacts"]),7); self.assertIn(missing,{x["path"] for x in state["artifacts"]})
                    (run/missing).unlink()
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.build_denominator(root,"r2-run")
                finally: holder.cleanup()
        bad={"artifact_type":"publication_state","schema_version":"2.0","run_id":"r","contract_sha256":"0"*64,
             "payload":{"status":"committed","contract_sha256":"0"*64,"artifacts":[]},"payload_sha256":""}
        bad["payload_sha256"]=analysis_pipeline._digest(bad["payload"])
        with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline._validate_artifact(bad)

    def test_issue_and_apply_revalidate_every_committed_denominator_member(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run)
            (run/"internal/source-bindings.json").unlink()
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.issue_context(root,"r2-run")
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
        finally: holder.cleanup()

    def test_recovery_rederives_new_state_from_fragment_not_self_consistent_journal(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run"); frag=self.fragment(run)
            os.environ["PANGEA_PIPELINE_FAULT"]="prepared"
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
            os.environ.pop("PANGEA_PIPELINE_FAULT")
            path=next((run/"internal/transactions").glob("*.json")); env=json.loads(path.read_text()); tx=env["payload"]
            changed=next(row for row in tx["new_ledger"]["obligations"] if row["obligation_id"] in {x["obligation_id"] for x in tx["new_selected_rows"]})
            changed["disposition"]["reason"]="self-consistent journal invention"
            tx["new_selected_rows"]=[row for row in tx["new_ledger"]["obligations"] if row["obligation_id"] in {x["obligation_id"] for x in tx["new_selected_rows"]}]
            tx["new_ledger_sha256"]=analysis_pipeline._digest(tx["new_ledger"]); tx["new_selected_rows_sha256"]=analysis_pipeline._digest(tx["new_selected_rows"])
            record=next(x for x in tx["new_assignment_index"]["assignments"] if x["fragment_id"]==tx["fragment_id"])
            record["applied_ledger_sha256"]=tx["new_ledger_sha256"]; tx["new_assignment_index_sha256"]=analysis_pipeline._digest(tx["new_assignment_index"])
            entry=next(x for x in tx["new_obligation_index"]["repositories"] if x["repository"]==tx["repository"])
            entry["ledger_sha256"]=tx["new_ledger_sha256"]; tx["new_obligation_index_sha256"]=analysis_pipeline._digest(tx["new_obligation_index"])
            env["payload_sha256"]=analysis_pipeline._digest(tx); path.chmod(0o600); path.write_text(json.dumps(env)); path.chmod(0o400)
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",frag)
        finally:
            os.environ.pop("PANGEA_PIPELINE_FAULT",None); holder.cleanup()

    def test_recovery_rederives_old_state_from_baseline_and_prior_fragments(self):
        holder,root,run=self.fixture(large=True); original=analysis_pipeline.context_budget.build
        try:
            analysis_pipeline.build_denominator(root,"r2-run")
            def capped(*args,**kwargs):
                if len(args[3])>3: raise analysis_pipeline.context_budget.ContextError("split")
                return original(*args,**kwargs)
            analysis_pipeline.context_budget.build=capped
            self.assertGreater(analysis_pipeline.issue_context(root,"r2-run")["assignments"],1)
            first=self.fragment(run,0); second=self.fragment(run,1)
            analysis_pipeline.apply_fragment(root,"r2-run",first)
            os.environ["PANGEA_PIPELINE_FAULT"]="prepared"
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",second)
            os.environ.pop("PANGEA_PIPELINE_FAULT")
            second_fid=json.loads(second.read_text())["fragment_id"]
            path=run/"internal/transactions"/f"{second_fid}.json"; env=json.loads(path.read_text()); tx=env["payload"]
            first_ids=set(json.loads(first.read_text())["obligation_ids"])
            changed=next(row for row in tx["old_ledger"]["obligations"] if row["obligation_id"] in first_ids)
            changed["disposition"]["reason"]="different but internally consistent prior result"
            matching=next(row for row in tx["new_ledger"]["obligations"] if row["obligation_id"]==changed["obligation_id"])
            matching["disposition"]["reason"]=changed["disposition"]["reason"]
            tx["old_ledger_sha256"]=analysis_pipeline._digest(tx["old_ledger"]); tx["new_ledger_sha256"]=analysis_pipeline._digest(tx["new_ledger"])
            old_entry=next(x for x in tx["old_obligation_index"]["repositories"] if x["repository"]==tx["repository"])
            new_entry=next(x for x in tx["new_obligation_index"]["repositories"] if x["repository"]==tx["repository"])
            old_entry["ledger_sha256"]=tx["old_ledger_sha256"]; new_entry["ledger_sha256"]=tx["new_ledger_sha256"]
            tx["old_obligation_index_sha256"]=analysis_pipeline._digest(tx["old_obligation_index"]); tx["new_obligation_index_sha256"]=analysis_pipeline._digest(tx["new_obligation_index"])
            env["payload_sha256"]=analysis_pipeline._digest(tx); path.chmod(0o600); path.write_text(json.dumps(env)); path.chmod(0o400)
            live=run/"internal/ledgers/driver.json"; live_env=json.loads(live.read_text()); live_row=next(row for row in live_env["payload"]["obligations"] if row["obligation_id"]==changed["obligation_id"])
            live_row["disposition"]["reason"]=changed["disposition"]["reason"]; live_env["payload_sha256"]=analysis_pipeline._digest(live_env["payload"])
            live.chmod(0o600); live.write_text(json.dumps(live_env)); live.chmod(0o400)
            with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.apply_fragment(root,"r2-run",second)
        finally:
            analysis_pipeline.context_budget.build=original; os.environ.pop("PANGEA_PIPELINE_FAULT",None); holder.cleanup()

    def test_candidate_closure_rejects_receipt_source_skill_and_output_schema_drift(self):
        holder,root,run=self.fixture()
        try:
            analysis_pipeline.build_denominator(root,"r2-run"); analysis_pipeline.issue_context(root,"r2-run")
            env=json.loads(next((run/"internal/context-packs").glob("*/CONTEXT.json")).read_text()); candidate=env["payload"]["candidate"]
            self.assertTrue(candidate["skill_receipts"]); self.assertTrue(candidate["injected"]["skills"])
            contract=analysis_pipeline._contract(run); inv,_,snapshot=analysis_pipeline._load_repo(run,contract,"driver")
            baseline=analysis_pipeline._baseline_ledger(run,contract,"driver",inv,snapshot); skills=analysis_pipeline._skills()
            mutations=(lambda x:x["skill_receipts"].pop(),lambda x:x["injected"]["sources"][0].update(text="forged"),
                       lambda x:x["injected"]["skills"][0].update(text="forged"),lambda x:x.update(output_schema="{}"))
            for mutate in mutations:
                bad=copy.deepcopy(candidate); mutate(bad)
                with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline._validate_candidate(bad,inv,baseline,snapshot,skills)
        finally: holder.cleanup()

    def test_duplicate_snapshot_repository_id_path_and_manifest_are_rejected(self):
        for duplicate in ("repository","snapshot_id","snapshot_dir","manifest"):
            with self.subTest(duplicate=duplicate):
                holder,root,run=self.fixture(multi=True)
                try:
                    path=run/"internal/source-snapshots.json"; receipt=json.loads(path.read_text()); first,second=receipt["snapshots"]
                    if duplicate=="repository": second["manifest"]["repository"]=first["manifest"]["repository"]
                    elif duplicate=="snapshot_id": second["snapshot_id"]=first["snapshot_id"]
                    elif duplicate=="snapshot_dir": second["snapshot_dir"]=first["snapshot_dir"]
                    else: second["manifest"]["content_sha256"]=first["manifest"]["content_sha256"]
                    path.write_text(json.dumps(receipt))
                    with self.assertRaises(analysis_pipeline.PipelineError): analysis_pipeline.build_denominator(root,"r2-run")
                finally: holder.cleanup()


if __name__ == "__main__": unittest.main()
