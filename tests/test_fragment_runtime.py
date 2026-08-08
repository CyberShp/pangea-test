from pathlib import Path
import copy,hashlib,json,tempfile,unittest
import jsonschema
from runtime import source_inventory,obligation_ledger,context_budget,fragment_runtime
ROOT=Path(__file__).resolve().parent/"fixtures/mini-storage-module"
SKILLS={"storage-resource-recovery":{"version":"1","content":"trusted resource rules"}}
class RuntimeTests(unittest.TestCase):
 def base(self,resource=False):
  inv=source_inventory.build(ROOT,"mini","c"*40);led=obligation_ledger.build(inv,ROOT);items={i["inventory_id"]:i for i in inv["items"]};kind="resource" if resource else "source_chunk";row=next(r for r in led["obligations"] if items[r["inventory_id"]]["kind"]==kind);item=items[row["inventory_id"]];rs=[]
  if resource:rs=[fragment_runtime.skill_receipt("storage-resource-recovery",[item["inventory_id"]],[row["obligation_id"]],SKILLS,"resource only")]
  rg={k:item[k] for k in ("inventory_id","path","line_start","line_end")};pack=context_budget.build(inv,led,ROOT,[row["obligation_id"]],[rg],rs,SKILLS,"run","frag")
  lines=(ROOT/item["path"]).read_text().splitlines() or [""];text="\n".join(lines[item["line_start"]-1:item["line_end"]]);line_count=item["line_end"]-item["line_start"]+1;fact={"obligation_id":row["obligation_id"],"inventory_id":item["inventory_id"],"path":item["path"],"line_start":item["line_start"],"line_count":line_count,"excerpt_sha256":hashlib.sha256(text.encode()).hexdigest(),"evidence":"bounded source evidence"};key=[row["obligation_id"],item["inventory_id"],item["line_start"],line_count]
  contribution_payload={"priority":"P0","obligation_id":row["obligation_id"],"fact_keys":[key],"summary":"source flow contribution","controls":["cli"],"oracles":["exit"]};families={name:[] for name in fragment_runtime.CONTRIBUTION_FAMILIES};families["flows"]=[{"contribution_id":fragment_runtime._canonical_id("C-",contribution_payload),**contribution_payload}];risk_payload={"severity":"Low","obligation_id":row["obligation_id"],"fact_keys":[key],"summary":"observed"}
  frag={"artifact_type":"analysis_fragment","schema_version":"2.0","worker_instance":"worker","run_id":"run","fragment_id":"frag","context_pack_sha256":context_budget.digest(pack),"obligation_ids":[row["obligation_id"]],"skill_receipt_ids":[r["receipt_id"] for r in rs],"facts":[fact],"contributions":families,"risk_cards":[{"risk_id":fragment_runtime._canonical_id("R-",risk_payload),**risk_payload}],"dispositions":[{"obligation_id":row["obligation_id"],"outcome":"analyzed","reason":"bounded source analyzed"}],"unresolved":[],"usage":{"output_tokens":10,"finish_reason":"stop","valid_json":True}}
  return inv,led,pack,rs,frag
 def test_context_unknown_obligation(self):
  inv,led,pack,rs,_=self.base();pack["obligation_ids"]=["OBL-unknown"]
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_noncovering_range(self):
  inv,led,pack,rs,_=self.base();pack["allowed_ranges"][0]["line_start"]+=1
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_overlapping_range(self):
  inv,led,pack,rs,_=self.base();pack["allowed_ranges"].append(copy.deepcopy(pack["allowed_ranges"][0]))
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_irrelevant_range(self):
  inv,led,pack,rs,_=self.base();other=next(i for i in inv["items"] if i["inventory_id"] not in pack["allowed_ranges"][0]["inventory_ids"]);pack["allowed_ranges"].append({"inventory_ids":[other["inventory_id"]],**{k:other[k] for k in ("path","line_start","line_end")}})
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_token_mismatch(self):
  inv,led,pack,rs,_=self.base();pack["input_budget_tokens"]+=1
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_coalesces_shared_source_window(self):
  inv=source_inventory.build(ROOT,"mini","c"*40);led=obligation_ledger.build(inv,ROOT);items={i["inventory_id"]:i for i in inv["items"]};chunk=next(i for i in inv["items"] if i["kind"]=="source_chunk" and i["path"]=="resource.c");signal=next(i for i in inv["items"] if i["kind"]=="branch" and i["path"]=="resource.c");rows=[next(r for r in led["obligations"] if r["inventory_id"]==i["inventory_id"]) for i in (chunk,signal)];ranges=[{k:i[k] for k in ("inventory_id","path","line_start","line_end")} for i in (chunk,signal)]
  pack=context_budget.build(inv,led,ROOT,[r["obligation_id"] for r in rows],ranges,[],{},"r","f")
  self.assertEqual(1,len(pack["allowed_ranges"]));self.assertEqual({chunk["inventory_id"],signal["inventory_id"]},set(pack["allowed_ranges"][0]["inventory_ids"]));self.assertEqual(1,len(pack["content_digests"]["sources"]))
 def test_context_bool_line_rejected(self):
  inv,led,pack,rs,_=self.base();pack["allowed_ranges"][0]["line_start"]=True
  schema=json.loads((Path(__file__).resolve().parents[1]/"schemas/context-pack.schema.json").read_text())
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(pack,schema)
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_schema_version_rejected(self):
  inv,led,pack,rs,_=self.base();pack["schema_version"]="2.0"
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_forged_receipt(self):
  inv,led,pack,rs,_=self.base(resource=True);rs[0]["content_sha256"]="0"*64
  with self.assertRaises((context_budget.ContextError,fragment_runtime.FragmentError)):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_context_counts_large_source(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x.c";p.write_text("x"*800001);inv=source_inventory.build(d,"plain","d"*40,["x.c"]);led=obligation_ledger.build(inv,d);row=led["obligations"][0];item=inv["items"][0]
   with self.assertRaises(context_budget.ContextError):context_budget.build(inv,led,d,[row["obligation_id"]],[{**{k:item[k] for k in ("inventory_id","path","line_start","line_end")}}],[],{},"r","f")
 def test_context_cjk_is_not_underestimated(self):
  with tempfile.TemporaryDirectory() as d:
   Path(d,"x.c").write_text("界"*55000);inv=source_inventory.build(d,"plain","d"*40,["x.c"]);led=obligation_ledger.build(inv,d);item=next(i for i in inv["items"] if i["kind"]=="source_chunk");row=next(r for r in led["obligations"] if r["inventory_id"]==item["inventory_id"])
   with self.assertRaises(context_budget.ContextError):context_budget.build(inv,led,d,[row["obligation_id"]],[{k:item[k] for k in ("inventory_id","path","line_start","line_end")}],[],{},"r","f")
 def test_budget_receipt_reserves_fixed_and_output_costs(self):
  _,_,pack,_,_=self.base();receipt=pack["budget_receipt"];self.assertEqual("utf8-json-byte-upper-bound",receipt["estimator_id"]);self.assertEqual("1.0",receipt["estimator_version"]);self.assertEqual(pack["input_budget_tokens"]+4096,receipt["total_context_upper_bound_tokens"]);self.assertEqual((12000,12000,4096),(receipt["system_prompt_reserved_tokens"],receipt["tool_schemas_reserved_tokens"],receipt["protocol_reserved_tokens"]))
 def test_budget_estimator_metadata_schema_python_parity(self):
  inv,led,pack,rs,_=self.base();pack["budget_receipt"]["estimator_id"]="bytes-div-four";schema=json.loads((Path(__file__).resolve().parents[1]/"schemas/context-pack.schema.json").read_text())
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(pack,schema)
  with self.assertRaises(context_budget.ContextError):context_budget.validate(pack,inv,led,ROOT,rs,SKILLS)
 def test_receipt_wrong_trigger(self):
  inv,led,_,_,_=self.base();row=led["obligations"][0];item=inv["items"][0];rec=fragment_runtime.skill_receipt("storage-resource-recovery",[item["inventory_id"]],[row["obligation_id"]],SKILLS,"x")
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate_receipt(rec,inv,led,SKILLS)
 def test_receipt_forged_id(self):
  inv,led,_,rs,_=self.base(resource=True);rs[0]["receipt_id"]="SR-"+"0"*16
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate_receipt(rs[0],inv,led,SKILLS)
 def test_receipt_id_binds_na_boundary(self):
  inv,led,_,rs,_=self.base(resource=True);old=rs[0]["receipt_id"];new=fragment_runtime.skill_receipt(rs[0]["skill_id"],rs[0]["trigger_inventory_ids"],rs[0]["obligation_ids"],SKILLS,"different")
  self.assertNotEqual(old,new["receipt_id"])
 def test_receipt_schema_version_rejected(self):
  inv,led,_,rs,_=self.base(resource=True);rs[0]["schema_version"]="2.0"
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate_receipt(rs[0],inv,led,SKILLS)
 def test_fragment_cannot_claim_receipt_outside_pack(self):
  inv,led,pack,rs,frag=self.base(resource=True);extra=fragment_runtime.skill_receipt(rs[0]["skill_id"],rs[0]["trigger_inventory_ids"],rs[0]["obligation_ids"],SKILLS,"other");frag["skill_receipt_ids"].append(extra["receipt_id"])
  with self.assertRaises((context_budget.ContextError,fragment_runtime.FragmentError)):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs+[extra],SKILLS)
 def test_duplicate_receipt_for_same_obligation_skill_rejected(self):
  inv,led,_,rs,frag=self.base(resource=True);original=rs[0];extra=fragment_runtime.skill_receipt(original["skill_id"],original["trigger_inventory_ids"],original["obligation_ids"],SKILLS,"different")
  item=next(i for i in inv["items"] if i["inventory_id"]==frag["facts"][0]["inventory_id"]);rg={k:item[k] for k in ("inventory_id","path","line_start","line_end")}
  with self.assertRaises(context_budget.ContextError):context_budget.build(inv,led,ROOT,frag["obligation_ids"],[rg],[original,extra],SKILLS,"r","f")
 def test_fragment_duplicate_disposition(self):
  inv,led,pack,rs,frag=self.base();frag["dispositions"].append(copy.deepcopy(frag["dispositions"][0]))
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_negative_usage(self):
  inv,led,pack,rs,frag=self.base();frag["usage"]["output_tokens"]=-1
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_bool_usage_rejected(self):
  inv,led,pack,rs,frag=self.base();frag["usage"]["output_tokens"]=True
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_zero_usage_rejected(self):
  inv,led,pack,rs,frag=self.base();frag["usage"]["output_tokens"]=0
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_source_chunk_partial_fact_cannot_close_denominator(self):
  inv,led,pack,rs,frag=self.base();fact=frag["facts"][0]
  if fact["line_count"]==1:self.skipTest("fixture chunk is one line")
  fact["line_count"]=1;text=(ROOT/fact["path"]).read_text().splitlines()[fact["line_start"]-1];fact["excerpt_sha256"]=hashlib.sha256(text.encode()).hexdigest();frag["contributions"]={name:[] for name in fragment_runtime.CONTRIBUTION_FAMILIES};frag["risk_cards"]=[]
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_unrelated_window_line_cannot_support_analyzed_or_na(self):
  inv=source_inventory.build(ROOT,"mini","c"*40);led=obligation_ledger.build(inv,ROOT);item=next(i for i in inv["items"] if i["kind"]=="entrypoint" and i["line_start"]>1 and not i["storage_skill_triggers"]);row=next(r for r in led["obligations"] if r["inventory_id"]==item["inventory_id"]);lines=(ROOT/item["path"]).read_text().splitlines();rg={"inventory_id":item["inventory_id"],"path":item["path"],"line_start":1,"line_end":len(lines)};pack=context_budget.build(inv,led,ROOT,[row["obligation_id"]],[rg],[],{},"r","unrelated");fact={"obligation_id":row["obligation_id"],"inventory_id":item["inventory_id"],"path":item["path"],"line_start":1,"line_end":1,"excerpt_sha256":hashlib.sha256(lines[0].encode()).hexdigest(),"evidence":"unrelated window line"};key=[row["obligation_id"],item["inventory_id"],1,1];base={"artifact_type":"analysis_fragment","schema_version":"1.0","worker_instance":"w","run_id":"r","fragment_id":"unrelated","context_pack_sha256":context_budget.digest(pack),"obligation_ids":[row["obligation_id"]],"skill_receipt_ids":[],"facts":[fact],"contributions":{"flows":[]},"risk_cards":[],"unresolved":[],"usage":{"output_tokens":8,"finish_reason":"stop","valid_json":True}}
  for disposition in ({"obligation_id":row["obligation_id"],"outcome":"analyzed","reason":"wrong line"},{"obligation_id":row["obligation_id"],"outcome":"not_applicable","reason":"wrong line","boundary":"scope","counterevidence_fact_keys":[key]}):
   with self.subTest(outcome=disposition["outcome"]):
    frag={**copy.deepcopy(base),"dispositions":[disposition]}
    with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,[],{})
 def test_na_counterevidence_itself_must_intersect_item(self):
  inv=source_inventory.build(ROOT,"mini","c"*40);led=obligation_ledger.build(inv,ROOT);item=next(i for i in inv["items"] if i["kind"]=="entrypoint" and i["line_start"]>1 and not i["storage_skill_triggers"]);row=next(r for r in led["obligations"] if r["inventory_id"]==item["inventory_id"]);lines=(ROOT/item["path"]).read_text().splitlines();pack=context_budget.build(inv,led,ROOT,[row["obligation_id"]],[{"inventory_id":item["inventory_id"],"path":item["path"],"line_start":1,"line_end":len(lines)}],[],{},"r","na-core");facts=[]
  for number in (1,item["line_start"]):facts.append({"obligation_id":row["obligation_id"],"inventory_id":item["inventory_id"],"path":item["path"],"line_start":number,"line_end":number,"excerpt_sha256":hashlib.sha256(lines[number-1].encode()).hexdigest(),"evidence":"source"})
  unrelated=[row["obligation_id"],item["inventory_id"],1,1];frag={"artifact_type":"analysis_fragment","schema_version":"1.0","worker_instance":"w","run_id":"r","fragment_id":"na-core","context_pack_sha256":context_budget.digest(pack),"obligation_ids":[row["obligation_id"]],"skill_receipt_ids":[],"facts":facts,"contributions":{"flows":[]},"risk_cards":[],"dispositions":[{"obligation_id":row["obligation_id"],"outcome":"not_applicable","reason":"wrong core fact","boundary":"scope","counterevidence_fact_keys":[unrelated]}],"unresolved":[],"usage":{"output_tokens":8,"finish_reason":"stop","valid_json":True}}
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,[],{})
 def test_fragment_schema_version_rejected(self):
  inv,led,pack,rs,frag=self.base();frag["schema_version"]="3.0"
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_fake_risk_evidence(self):
  inv,led,pack,rs,frag=self.base();frag["risk_cards"][0]["fact_keys"][0][-1]+=1
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_p0_missing_oracle(self):
  inv,led,pack,rs,frag=self.base();frag["contributions"]["flows"][0]["oracles"]=[]
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_requires_all_contribution_families(self):
  inv,led,pack,rs,frag=self.base();frag["contributions"].pop("coverage")
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_high_risk_requires_complete_causal_and_blackbox_chain(self):
  inv,led,pack,rs,frag=self.base();old=frag["risk_cards"][0];payload={"severity":"High","obligation_id":old["obligation_id"],"fact_keys":old["fact_keys"],"summary":"state remains failed","trigger":"invalid request","propagation":"state remains set","impact":"next request fails","observation":"return and log","recovery":"valid request restores","control":"invalid then valid request","oracle":"first fails second succeeds"};frag["risk_cards"]=[{"risk_id":fragment_runtime._canonical_id("R-",payload),**payload}]
  fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
  frag["risk_cards"][0].pop("oracle")
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_weak_na(self):
  inv,led,pack,rs,frag=self.base();frag["facts"]=[];frag["dispositions"]=[{"obligation_id":frag["obligation_ids"][0],"outcome":"not_applicable","reason":"x","boundary":"scope","counterevidence":""}]
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_structured_na_happy_and_persisted(self):
  inv,led,pack,rs,frag=self.base();key=frag["contributions"]["flows"][0]["fact_keys"][0];oid=frag["obligation_ids"][0];frag["dispositions"]=[{"obligation_id":oid,"outcome":"not_applicable","reason":"absent in bounded chunk","boundary":"selected source chunk","counterevidence_fact_keys":[key]}]
  out=fragment_runtime.validate_and_apply(led,frag,pack,inv,ROOT,rs,SKILLS);row=next(r for r in out["obligations"] if r["obligation_id"]==oid);self.assertEqual([key],row["disposition"]["counterevidence_fact_keys"])
 def test_fragment_covered_cycle_rejected(self):
  inv=source_inventory.build(ROOT,"mini","c"*40);led=obligation_ledger.build(inv,ROOT);items={i["inventory_id"]:i for i in inv["items"]};selected=[i for i in inv["items"] if i["kind"]=="source_chunk" and not i["storage_skill_triggers"]][:2];rows=[next(r for r in led["obligations"] if r["inventory_id"]==i["inventory_id"]) for i in selected];pack=context_budget.build(inv,led,ROOT,[r["obligation_id"] for r in rows],[{k:i[k] for k in ("inventory_id","path","line_start","line_end")} for i in selected],[],{},"r","cycle");a,b=[r["obligation_id"] for r in rows]
  frag={"artifact_type":"analysis_fragment","schema_version":"1.0","worker_instance":"w","run_id":"r","fragment_id":"cycle","context_pack_sha256":context_budget.digest(pack),"obligation_ids":[a,b],"skill_receipt_ids":[],"facts":[],"contributions":{"flows":[]},"risk_cards":[],"dispositions":[{"obligation_id":a,"outcome":"covered_by_other","reason":"b","covered_by":b},{"obligation_id":b,"outcome":"covered_by_other","reason":"a","covered_by":a}],"unresolved":[],"usage":{"output_tokens":10,"finish_reason":"stop","valid_json":True}}
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,[],{})
 def test_covered_by_existing_analyzed_fragment_rejects_distinct_action(self):
  inv=source_inventory.build(ROOT,"mini","c"*40);led=obligation_ledger.build(inv,ROOT);item=next(i for i in inv["items"] if i["kind"]=="entrypoint" and not i["storage_skill_triggers"]);rows=[r for r in led["obligations"] if r["inventory_id"]==item["inventory_id"]][:2];rg={k:item[k] for k in ("inventory_id","path","line_start","line_end")};pack=context_budget.build(inv,led,ROOT,[rows[0]["obligation_id"]],[rg],[],{},"run","frag1");lines=(ROOT/item["path"]).read_text().splitlines() or [""];line_count=item["line_end"]-item["line_start"]+1;text="\n".join(lines[item["line_start"]-1:item["line_end"]]);fact={"obligation_id":rows[0]["obligation_id"],"inventory_id":item["inventory_id"],"path":item["path"],"line_start":item["line_start"],"line_count":line_count,"excerpt_sha256":hashlib.sha256(text.encode()).hexdigest(),"evidence":"bounded source evidence"};families={name:[] for name in fragment_runtime.CONTRIBUTION_FAMILIES};first={"artifact_type":"analysis_fragment","schema_version":"2.0","worker_instance":"worker1","run_id":"run","fragment_id":"frag1","context_pack_sha256":context_budget.digest(pack),"obligation_ids":[rows[0]["obligation_id"]],"skill_receipt_ids":[],"facts":[fact],"contributions":families,"risk_cards":[],"dispositions":[{"obligation_id":rows[0]["obligation_id"],"outcome":"analyzed","reason":"bounded source analyzed"}],"unresolved":[],"usage":{"output_tokens":8,"finish_reason":"stop","valid_json":True}};after=fragment_runtime.validate_and_apply(led,first,pack,inv,ROOT,[],{});target=rows[0]["obligation_id"];second_row=rows[1];pack2=context_budget.build(inv,after,ROOT,[second_row["obligation_id"]],[rg],[],{},"run","frag2")
  second={"artifact_type":"analysis_fragment","schema_version":"2.0","worker_instance":"worker2","run_id":"run","fragment_id":"frag2","context_pack_sha256":context_budget.digest(pack2),"obligation_ids":[second_row["obligation_id"]],"skill_receipt_ids":[],"facts":[],"contributions":families,"risk_cards":[],"dispositions":[{"obligation_id":second_row["obligation_id"],"outcome":"covered_by_other","reason":"same inventory behavior","covered_by":target}],"unresolved":[],"usage":{"output_tokens":8,"finish_reason":"stop","valid_json":True}}
  with self.assertRaisesRegex(fragment_runtime.FragmentError,"covered_by may not erase a distinct action dimension"):
   fragment_runtime.validate_and_apply(after,second,pack2,inv,ROOT,[],{})
 def test_trusted_skill_shape_rejected(self):
  inv,led,pack,rs,frag=self.base(resource=True);bad=copy.deepcopy(SKILLS);bad["storage-resource-recovery"]["path"]="untrusted"
  with self.assertRaises((context_budget.ContextError,fragment_runtime.FragmentError)):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,bad)
 def test_fragment_blocked_requires_unresolved(self):
  inv,led,pack,rs,frag=self.base();frag["facts"]=[];frag["dispositions"]=[{"obligation_id":frag["obligation_ids"][0],"outcome":"blocked","reason":"missing build"}]
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_analyzed_rejects_unresolved(self):
  inv,led,pack,rs,frag=self.base();frag["unresolved"]=[{"obligation_id":frag["obligation_ids"][0],"reason":"x","next_step":"y"}]
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_fragment_cross_obligation_fact(self):
  inv,led,pack,rs,frag=self.base();other=next(r for r in led["obligations"] if r["inventory_id"]!=frag["facts"][0]["inventory_id"]);frag["facts"][0]["obligation_id"]=other["obligation_id"]
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_validate_and_apply_uses_fragment_id(self):
  inv,led,pack,rs,frag=self.base();out=fragment_runtime.validate_and_apply(led,frag,pack,inv,ROOT,rs,SKILLS);row=next(r for r in out["obligations"] if r["obligation_id"]==frag["obligation_ids"][0]);self.assertEqual("frag",row["assigned_fragment_id"])
 def test_merge_conflict_is_atomic(self):
  inv,led,pack,rs,frag=self.base();out=fragment_runtime.validate_and_apply(led,frag,pack,inv,ROOT,rs,SKILLS)
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger._apply_validated(out,frag,inv,ROOT)
  self.assertEqual("pending",led["obligations"][0]["status"])
 def test_schema_parity_happy_artifacts(self):
  inv,led,pack,rs,frag=self.base(resource=True);root=Path(__file__).resolve().parents[1]/"schemas"
  for value,name in ((inv,"source-inventory.schema.json"),(led,"obligation-ledger.schema.json"),(pack,"context-pack.schema.json"),(rs[0],"skill-receipt.schema.json"),(frag,"analysis-fragment.schema.json")):
   jsonschema.validate(value,json.loads((root/name).read_text()))
 def test_schema_and_python_reject_nested_extra(self):
  inv,led,pack,rs,frag=self.base();frag["facts"][0]["extra"]=1;schema=json.loads((Path(__file__).resolve().parents[1]/"schemas/analysis-fragment.schema.json").read_text())
  with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(frag,schema)
  with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(frag,pack,inv,led,ROOT,rs,SKILLS)
 def test_line_count_schema_python_parity(self):
  inv,led,pack,rs,frag=self.base();schema=json.loads((Path(__file__).resolve().parents[1]/"schemas/analysis-fragment.schema.json").read_text())
  for mutation in (lambda fact:fact.update(line_count=0),lambda fact:fact.update(line_count=True),lambda fact:fact.update(line_end=fact.pop("line_count"))):
   bad=copy.deepcopy(frag);mutation(bad["facts"][0])
   with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(bad,schema)
   with self.assertRaises(fragment_runtime.FragmentError):fragment_runtime.validate(bad,pack,inv,led,ROOT,rs,SKILLS)
 def test_merged_hash_binds_full_body_not_only_ids(self):
  _,_,_,_,frag=self.base();first=fragment_runtime.merge_fragments([frag]);changed=copy.deepcopy(frag)
  changed["contributions"]["flows"][0]["summary"]="same id with changed body"
  second=fragment_runtime.merge_fragments([changed]);self.assertNotEqual(first["sha256"],second["sha256"])
  changed=copy.deepcopy(frag);changed["risk_cards"][0]["summary"]="same risk id with changed body"
  self.assertNotEqual(first["sha256"],fragment_runtime.merge_fragments([changed])["sha256"])
