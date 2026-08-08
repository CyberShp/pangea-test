from pathlib import Path
import copy,json,os,tempfile,unittest
import jsonschema
from runtime import source_inventory
ROOT=Path(__file__).resolve().parent/"fixtures/mini-storage-module"
class SourceInventoryTests(unittest.TestCase):
 def test_inventory_omission_rejected(self):
  inv=source_inventory.build(ROOT,"mini","a"*40);inv["items"].pop()
  with self.assertRaises(source_inventory.InventoryError):source_inventory.validate(inv,ROOT)
 def test_forged_inventory_id_rejected(self):
  inv=source_inventory.build(ROOT,"mini","a"*40);inv["items"][0]["inventory_id"]="INV-"+"0"*16
  with self.assertRaises(source_inventory.InventoryError):source_inventory.validate(inv,ROOT)
 def test_missing_scope_rejected(self):
  with self.assertRaises(source_inventory.InventoryError):source_inventory.build(ROOT,"mini","a"*40,["missing.c"])
 def test_root_symlink_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   link=Path(d)/"root";link.symlink_to(ROOT,target_is_directory=True)
   with self.assertRaises(source_inventory.InventoryError):source_inventory.build(link,"mini","a"*40,["connection.c"])
 def test_parent_symlink_escape_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/"root";outside=Path(d)/"outside";root.mkdir();outside.mkdir();(outside/"x.c").write_text("int x;");(root/"jump").symlink_to(outside,target_is_directory=True)
   with self.assertRaises(source_inventory.InventoryError):source_inventory.build(root,"mini","a"*40,["jump/x.c"])
 def test_fifo_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   fifo=Path(d)/"x.c";os.mkfifo(fifo)
   with self.assertRaises(source_inventory.InventoryError):source_inventory.build(d,"mini","a"*40,["x.c"])
 def _skills(self,repo,text="int x;"):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x.c";p.write_text(text);inv=source_inventory.build(d,repo,"a"*40,["x.c"]);return {s for i in inv["items"] for s in i["storage_skill_triggers"]}
 def test_spdk_repository_trigger(self):self.assertIn("storage-spdk",self._skills("spdk"))
 def test_nvme_cli_repository_trigger(self):self.assertIn("storage-nvme-cli",self._skills("nvme-cli"))
 def test_nvmeof_iscsi_destructive_triggers(self):
  skills=self._skills("plain","nvmf iscsi sanitize");self.assertTrue({"storage-nvmeof","storage-iscsi","storage-destructive-cli"}<=skills)
 def test_resource_and_negative_trigger(self):
  self.assertIn("storage-resource-recovery",self._skills("plain","void x(){ free(p); }"));self.assertEqual(set(),self._skills("plain"))
 def test_translation_unit_denominator(self):
  inv=source_inventory.build(ROOT,"mini","a"*40);chunks=[i for i in inv["items"] if i["kind"]=="source_chunk"]
  self.assertEqual(set(inv["scope"]),{i["path"] for i in chunks})
 def test_fixed_chunks_cover_large_and_empty_files(self):
  with tempfile.TemporaryDirectory() as d:
   Path(d,"large.c").write_text("\n".join(f"int x{i};" for i in range(401)));Path(d,"empty.c").write_text("")
   inv=source_inventory.build(d,"plain","a"*40);chunks=[i for i in inv["items"] if i["kind"]=="source_chunk"]
   self.assertEqual([(1,200),(201,400),(401,401)],sorted((i["line_start"],i["line_end"]) for i in chunks if i["path"]=="large.c"))
   self.assertEqual((1,1),next((i["line_start"],i["line_end"]) for i in chunks if i["path"]=="empty.c"))
 def test_default_scope_ignores_symlink_directory(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d,"root");outside=Path(d,"outside");root.mkdir();outside.mkdir();Path(root,"ok.c").write_text("int ok;");Path(outside,"bad.c").write_text("int bad;");Path(root,"jump").symlink_to(outside,target_is_directory=True)
   self.assertEqual(["ok.c"],source_inventory.build(root,"plain","a"*40)["scope"])
 def test_repository_shape_rejected(self):
  with self.assertRaises(source_inventory.InventoryError):source_inventory.build(ROOT,"../bad","a"*40)
 def test_schema_version_rejected(self):
  inv=source_inventory.build(ROOT,"mini","a"*40);inv["schema_version"]="2.0"
  with self.assertRaises(source_inventory.InventoryError):source_inventory.validate(inv,ROOT)
 def test_repository_python_schema_parity(self):
  schema=json.loads((Path(__file__).resolve().parents[1]/"schemas/source-inventory.schema.json").read_text());valid=source_inventory.build(ROOT,"repo-1.test_2","a"*40);jsonschema.validate(valid,schema)
  for bad in ("Bad","owner/repo","a"*65):
   with self.subTest(bad=bad):
    with self.assertRaises(source_inventory.InventoryError):source_inventory.build(ROOT,bad,"a"*40)
    mutated=copy.deepcopy(valid);mutated["repository"]=bad
    for item in mutated["items"]:item["repository"]=bad
    with self.assertRaises(source_inventory.InventoryError):source_inventory.validate(mutated,ROOT)
    with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(mutated,schema)
