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
    return {"items":items},{"obligations":obligations},source.parent


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
            native={"v":1,"i":[[ordinal,"no issue"] for ordinal in first["item_ordinals"]],
                    "a":[[ordinal,"A","action meaning closed"] for ordinal in first["action_ordinals"]],"c":[]}
            action_map={row["ordinal"]:row for row in mapping["actions"]}
            pack={"run_id":"run-fixed","fragment_id":first["fragment_id"],
                  "obligation_ids":[action_map[x]["obligation_id"] for x in first["action_ordinals"]],"skill_receipts":[]}
            with self.assertRaises(compact_protocol.CompactProtocolError):
                compact_protocol.expand_native(native,first["compact_context"],mapping,pack)
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


if __name__=="__main__": unittest.main()
