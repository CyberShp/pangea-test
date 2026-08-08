from pathlib import Path
import copy,hashlib,tempfile,unittest
from runtime import source_inventory,obligation_ledger
ROOT=Path(__file__).resolve().parent/"fixtures/mini-storage-module"
class LedgerTests(unittest.TestCase):
 def setUp(self):self.inv=source_inventory.build(ROOT,"mini","b"*40);self.led=obligation_ledger.build(self.inv,ROOT)
 def test_missing_obligation_rejected(self):
  self.led["obligations"].pop()
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
 def test_unknown_obligation_rejected(self):
  self.led["obligations"][0]["obligation_id"]="OBL-unknown"
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
 def test_duplicate_obligation_rejected(self):
  self.led["obligations"][-1]=copy.deepcopy(self.led["obligations"][0])
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
 def test_assigned_premature_evidence_rejected(self):
  r=self.led["obligations"][0];r.update(status="assigned",assigned_fragment_id="f",assigned_worker_id="w",evidence=[{"fake":1}])
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
 def test_weak_na_rejected(self):
  r=self.led["obligations"][0];r.update(status="complete",assigned_fragment_id="f",assigned_worker_id="w",disposition={"outcome":"not_applicable","reason":"x","boundary":"","counterevidence":"x"})
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
 def test_finalize_full_ledger(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"plain.c";p.write_text("int value;");inv=source_inventory.build(d,"plain","e"*40,["plain.c"]);led=obligation_ledger.build(inv,d);item=inv["items"][0]
   for r in led["obligations"]:
    fact={"obligation_id":r["obligation_id"],"inventory_id":item["inventory_id"],"path":"plain.c","line_start":1,"line_count":1,"excerpt_sha256":hashlib.sha256(b"int value;").hexdigest(),"evidence":"source"}
    r.update(status="complete",assigned_fragment_id="f",assigned_worker_id="w",evidence=[fact],disposition={"outcome":"analyzed","reason":"source"})
   obligation_ledger.finalize(led,inv,d)
 def _complete_pair(self):
  items={i["inventory_id"]:i for i in self.inv["items"]};item=next(i for i in self.inv["items"] if i["kind"]=="entrypoint" and not i["storage_skill_triggers"]);rows=[r for r in self.led["obligations"] if r["inventory_id"]==item["inventory_id"]][:2]
  for row in rows:
   lines=(ROOT/item["path"]).read_text().splitlines() or [""];text="\n".join(lines[item["line_start"]-1:item["line_end"]]);fact={"obligation_id":row["obligation_id"],"inventory_id":item["inventory_id"],"path":item["path"],"line_start":item["line_start"],"line_count":item["line_end"]-item["line_start"]+1,"excerpt_sha256":hashlib.sha256(text.encode()).hexdigest(),"evidence":"source"};row.update(status="complete",assigned_fragment_id="f",assigned_worker_id="w",evidence=[fact])
  return rows
 def test_covered_by_cycle_rejected(self):
  a,b=self._complete_pair();a["disposition"]={"outcome":"covered_by_other","reason":"b","covered_by":b["obligation_id"]};b["disposition"]={"outcome":"covered_by_other","reason":"a","covered_by":a["obligation_id"]}
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
 def test_covered_by_chain_to_analyzed_accepted(self):
  a,b=self._complete_pair();a["disposition"]={"outcome":"analyzed","reason":"source"};b["evidence"]=[];b["disposition"]={"outcome":"covered_by_other","reason":"same path","covered_by":a["obligation_id"]}
  obligation_ledger.validate(self.led,self.inv,ROOT)
 def _complete(self,row,item,outcome):
  lines=(ROOT/item["path"]).read_text().splitlines() or [""];text="\n".join(lines[item["line_start"]-1:item["line_end"]]);fact={"obligation_id":row["obligation_id"],"inventory_id":item["inventory_id"],"path":item["path"],"line_start":item["line_start"],"line_count":item["line_end"]-item["line_start"]+1,"excerpt_sha256":hashlib.sha256(text.encode()).hexdigest(),"evidence":"source"};row.update(status="complete",assigned_fragment_id="f",assigned_worker_id="w",evidence=[fact],disposition=outcome)
 def test_source_chunk_cannot_be_covered(self):
  items={i["inventory_id"]:i for i in self.inv["items"]};chunk=next(i for i in self.inv["items"] if i["kind"]=="source_chunk" and not i["storage_skill_triggers"]);target=next(i for i in self.inv["items"] if i["kind"]=="entrypoint" and not i["storage_skill_triggers"]);a=next(r for r in self.led["obligations"] if r["inventory_id"]==chunk["inventory_id"]);b=next(r for r in self.led["obligations"] if r["inventory_id"]==target["inventory_id"]);self._complete(b,target,{"outcome":"analyzed","reason":"source"});self._complete(a,chunk,{"outcome":"covered_by_other","reason":"same","covered_by":b["obligation_id"]})
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
 def test_covered_by_cross_inventory_rejected(self):
  selected=[i for i in self.inv["items"] if i["kind"]=="entrypoint" and not i["storage_skill_triggers"]][:2];a,b=[next(r for r in self.led["obligations"] if r["inventory_id"]==item["inventory_id"]) for item in selected];self._complete(a,selected[0],{"outcome":"analyzed","reason":"source"});self._complete(b,selected[1],{"outcome":"covered_by_other","reason":"similar","covered_by":a["obligation_id"]})
  with self.assertRaises(obligation_ledger.LedgerError):obligation_ledger.validate(self.led,self.inv,ROOT)
