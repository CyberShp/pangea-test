from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from runtime import compact_protocol


def _equivalent_fixture(root:Path,count:int=820):
    source=root/"scope.c";source.write_text("\n".join(f"int local_{i};" for i in range(count))+"\n")
    items=[];obligations=[]
    for index in range(count):
        iid=f"INV-{index:016x}";items.append({"inventory_id":iid,"path":"scope.c","line_start":index+1,"line_end":index+1})
        actions=("explain","disconfirm") if index<min(count,788) else ("cover_source",)
        for offset,action in enumerate(actions):
            obligations.append({"obligation_id":f"OBL-{index*2+offset:016x}","inventory_id":iid,"action":action})
    return {"repository":"driver","commit":"a"*40,"items":items},{"obligations":obligations},source.parent


class CompactProtocolTests(unittest.TestCase):
    def test_spdk_equivalent_capacity_and_rich_maximum_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory,ledger,snapshot=_equivalent_fixture(Path(temp))
            plan,contexts=compact_protocol.capacity_plan(inventory,ledger,snapshot,"run-fixed")
            self.assertEqual(29,plan["analysis_worker_calls"])
            self.assertEqual(1,plan["semantic_auditor_calls"])
            self.assertEqual(37,plan["worst_model_calls"])
            self.assertEqual((12,32),(compact_protocol.EVIDENCE_MIN_BYTES,compact_protocol.EVIDENCE_MAX_BYTES))
            self.assertEqual((12,32),(compact_protocol.SEMANTIC_MIN_BYTES,compact_protocol.SEMANTIC_MAX_BYTES))
            self.assertEqual((12,32),(compact_protocol.CLAIM_MIN_BYTES,compact_protocol.CLAIM_MAX_BYTES))
            self.assertTrue(all(len(compact_protocol.canonical_bytes(row["compact_context"]))<=180000 for row in contexts))
            mapping=compact_protocol.ordinal_map(inventory,ledger)
            first=max(contexts,key=lambda row:len(compact_protocol.canonical_bytes(
                compact_protocol.maximum_native_output(row["compact_context"],mapping))))
            compact=first["compact_context"]
            native=compact_protocol.maximum_native_output(compact,mapping)
            maximum_bytes=len(compact_protocol.canonical_bytes(native))
            self.assertEqual(plan["maximum_native_output_bytes"],maximum_bytes)
            self.assertGreaterEqual(maximum_bytes,4000)
            self.assertLessEqual(maximum_bytes,4096)
            action_map={row["ordinal"]:row for row in mapping["actions"]}
            pack={"run_id":"run-fixed","fragment_id":first["fragment_id"],
                  "repository":"driver","commit":"a"*40,
                  "obligation_ids":[action_map[x]["obligation_id"] for x in first["action_ordinals"]],"skill_receipts":[]}
            expanded=compact_protocol.expand_native(native,compact,mapping,pack)
            self.assertEqual(len(first["action_ordinals"]),len(expanded["facts"]))
            self.assertEqual(set(pack["obligation_ids"]),{row["obligation_id"] for row in expanded["dispositions"]})
            self.assertEqual(1,len(expanded["risk_cards"]))
            self.assertEqual("Critical",expanded["risk_cards"][0]["severity"])
            self.assertFalse(any(expanded["contributions"].values()))

    def test_bare_min_generic_and_over_capacity_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory,ledger,snapshot=_equivalent_fixture(Path(temp),29)
            _,contexts=compact_protocol.capacity_plan(inventory,ledger,snapshot,"run-fixed")
            mapping=compact_protocol.ordinal_map(inventory,ledger); first=contexts[0]
            native={"v":1,"i":[[ordinal,"specific branch returns error"] for ordinal in first["item_ordinals"]],
                    "a":[[ordinal,"A","action meaning closed"] for ordinal in first["action_ordinals"]],"c":[]}
            action_map={row["ordinal"]:row for row in mapping["actions"]}
            pack={"run_id":"run-fixed","fragment_id":first["fragment_id"],
                  "repository":"driver","commit":"a"*40,
                  "obligation_ids":[action_map[x]["obligation_id"] for x in first["action_ordinals"]],"skill_receipts":[]}
            expanded=compact_protocol.expand_native(native,first["compact_context"],mapping,pack)
            self.assertEqual(len(first["item_ordinals"]),len({fact["inventory_id"] for fact in expanded["facts"]}))
            for generic in ("nothing found.","No Issue!","not applicable...","!!! nothing    found !!!"):
                rejected=deepcopy(native);rejected["i"][0][1]=generic
                with self.subTest(generic=generic):
                    with self.assertRaises(compact_protocol.CompactProtocolError):
                        compact_protocol.expand_native(rejected,first["compact_context"],mapping,pack)
            concrete=deepcopy(native);concrete["i"][0][1]="No issue in timeout branch"
            compact_protocol.expand_native(concrete,first["compact_context"],mapping,pack)
        with tempfile.TemporaryDirectory() as temp:
            inventory,ledger,snapshot=_equivalent_fixture(Path(temp),900)
            with self.assertRaisesRegex(compact_protocol.CompactProtocolError,"call closure"):
                compact_protocol.capacity_plan(inventory,ledger,snapshot,"run-fixed")

    def test_ordinal_projection_is_packing_order_invariant(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory,ledger,_=_equivalent_fixture(Path(temp),40)
            expected=compact_protocol.ordinal_map(inventory,ledger)
            shuffled_inventory={"items":list(reversed(inventory["items"]))}
            shuffled_ledger={"obligations":list(reversed(ledger["obligations"]))}
            self.assertEqual(expected,compact_protocol.ordinal_map(shuffled_inventory,shuffled_ledger))

    def test_fragment_identity_binds_repository_commit_and_ordinal_map(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory,ledger,snapshot=_equivalent_fixture(Path(temp),40)
            _,first=compact_protocol.capacity_plan(inventory,ledger,snapshot,"run-fixed")
            second_inventory=deepcopy(inventory);second_inventory["repository"]="second"
            _,second=compact_protocol.capacity_plan(second_inventory,ledger,snapshot,"run-fixed")
            self.assertTrue({row["fragment_id"] for row in first}.isdisjoint(
                {row["fragment_id"] for row in second}))
            changed_commit=deepcopy(inventory);changed_commit["commit"]="b"*40
            _,changed=compact_protocol.capacity_plan(changed_commit,ledger,snapshot,"run-fixed")
            self.assertNotEqual([row["fragment_id"] for row in first],[row["fragment_id"] for row in changed])
            changed_ledger=deepcopy(ledger);changed_ledger["obligations"][0]["action"]="changed-action"
            _,remapped=compact_protocol.capacity_plan(inventory,changed_ledger,snapshot,"run-fixed")
            self.assertNotEqual([row["fragment_id"] for row in first],[row["fragment_id"] for row in remapped])
            mapping=compact_protocol.ordinal_map(inventory,ledger);planned=first[0]
            native=compact_protocol.maximum_native_output(planned["compact_context"],mapping)
            action_map={row["ordinal"]:row for row in mapping["actions"]}
            pack={"run_id":"run-fixed","fragment_id":planned["fragment_id"],"repository":"driver",
                  "commit":"a"*40,"obligation_ids":[action_map[x]["obligation_id"] for x in planned["action_ordinals"]],
                  "skill_receipts":[]}
            for mutated_pack,mutated_mapping in (
                ({**pack,"repository":"second"},mapping),
                (pack,{**mapping,"items":[{**mapping["items"][0],"path":"changed.c"},*mapping["items"][1:]]}),
            ):
                with self.assertRaisesRegex(compact_protocol.CompactProtocolError,"identity"):
                    compact_protocol.expand_native(native,planned["compact_context"],mutated_mapping,mutated_pack)


if __name__=="__main__": unittest.main()
