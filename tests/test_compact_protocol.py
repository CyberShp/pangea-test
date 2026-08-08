from __future__ import annotations

from copy import deepcopy
import hashlib
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


def _static_candidate_fixture():
    inventory={"repository":"repo","commit":"a"*40,"items":[
        {"inventory_id":"INV-0000000000000001","path":"a.c","line_start":1,"line_end":1},
        {"inventory_id":"INV-0000000000000002","path":"a.c","line_start":2,"line_end":3},
        {"inventory_id":"INV-0000000000000003","path":"b.c","line_start":2,"line_end":2},
    ]}
    ledger={"obligations":[
        {"obligation_id":"OBL-0000000000000001","inventory_id":"INV-0000000000000001","action":"first action"},
        {"obligation_id":"OBL-0000000000000002","inventory_id":"INV-0000000000000001","action":"second action"},
        {"obligation_id":"OBL-0000000000000003","inventory_id":"INV-0000000000000002","action":"third action"},
        {"obligation_id":"OBL-0000000000000004","inventory_id":"INV-0000000000000003","action":"fourth action"},
    ]}
    mapping=compact_protocol.ordinal_map(inventory,ledger);item_ordinals=[0,1,2]
    fragment_id=compact_protocol.fragment_identity("run","repo","a"*40,mapping,item_ordinals)
    compact={"v":1,"f":fragment_id,"s":[
        [0,"a.c",1,1,"first"],[1,"a.c",2,3,"second\nthird"],[2,"b.c",2,2,"other"],
    ],"k":[],"i":[
        [0,[[0,"first action"],[1,"second action"]]],
        [1,[[2,"third action"]]],[2,[[3,"fourth action"]]],
    ],"q":compact_protocol._query_contract()}
    ranges=[
        {"inventory_ids":["INV-0000000000000001","INV-0000000000000002"],
         "path":"a.c","line_start":1,"line_end":3},
        {"inventory_ids":["INV-0000000000000003"],"path":"b.c","line_start":2,"line_end":2},
    ]
    pack={"run_id":"run","repository":"repo","commit":"a"*40,"fragment_id":fragment_id,
          "obligation_ids":[f"OBL-{ordinal:016x}" for ordinal in range(1,5)],
          "allowed_ranges":ranges,"skill_receipts":[]}
    injected={"sources":[
        {**ranges[0],"sha256":hashlib.sha256(b"first\nsecond\nthird").hexdigest(),
         "text":"first\nsecond\nthird"},
        {**ranges[1],"sha256":hashlib.sha256(b"other").hexdigest(),"text":"other"},
    ],"skills":[]}
    schema=compact_protocol.analysis_fragment_schema()
    candidate={"protocol_version":compact_protocol.CANDIDATE_PROTOCOL_VERSION,
               "output_schema":schema,"output_schema_sha256":hashlib.sha256(schema.encode()).hexdigest(),
               "instructions":compact_protocol.CANDIDATE_INSTRUCTIONS,"context_pack":pack,
               "skill_receipts":[],"injected":injected,"compact_context":compact,
               "compact_context_sha256":compact_protocol.digest(compact),"ordinal_map":mapping,
               "ordinal_map_sha256":compact_protocol.digest(mapping),"adapter_version":compact_protocol.VERSION}
    return candidate,deepcopy(mapping)


class CompactProtocolTests(unittest.TestCase):
    def test_native_canonicalization_repairs_only_complete_semantic_projection(self):
        candidate,_=_static_candidate_fixture();compact=candidate["compact_context"]
        action_ordinals=sorted(action[0] for row in compact["i"] for action in row[1])
        raw={"v":1,"i":[[row[0],"E"*16] for row in compact["i"]],
             "a":[[ordinal,"A","S"*16] for ordinal in reversed(action_ordinals)],
             "c":[["C","f","P1",action_ordinals[0],"semantic finding","bounded control","bounded oracle"]]}
        raw["i"][0][1]="evidence text"
        raw["a"][0][2]="semantic result with bounded detail"
        canonical=compact_protocol.canonicalize_native(raw,compact)
        self.assertEqual([row[0] for row in compact["i"]],[row[0] for row in canonical["i"]])
        self.assertEqual(action_ordinals,[row[0] for row in canonical["a"]])
        self.assertEqual("semantic result with bounded",canonical["a"][-1][2])
        unicode_safe=compact_protocol._normalize_native_text("证据 evidence with bounded detail")
        self.assertLessEqual(len(unicode_safe.encode()),32);unicode_safe.encode("utf-8").decode("utf-8")
        self.assertLessEqual(len(compact_protocol.canonical_bytes(canonical)),compact_protocol.NATIVE_OUTPUT_BYTE_LIMIT)
        compact_protocol.expand_native(canonical,compact,candidate["ordinal_map"],candidate["context_pack"])

        derived_compact={"i":[[ordinal,[[ordinal*2,"first action"],[ordinal*2+1,"second action"]]]
                               for ordinal in range(28)]}
        derived_base={"v":1,"i":[[ordinal,"E"*16] for ordinal in range(28)],
                      "a":[[ordinal,"A","S"*16] for ordinal in range(56)],"c":[]}
        for missing in (0,1,2):
            value=deepcopy(derived_base);value["i"]=value["i"][missing:]
            with self.subTest(derived_items=missing):
                projected=compact_protocol.canonicalize_native(value,derived_compact)
                self.assertEqual(28,len(projected["i"]))
        for missing in (3,28):
            value=deepcopy(derived_base);value["i"]=value["i"][missing:]
            with self.subTest(rejected_derived_items=missing):
                with self.assertRaisesRegex(compact_protocol.CompactProtocolError,"derived item limit"):
                    compact_protocol.canonicalize_native(value,derived_compact)
        official_005=deepcopy(derived_base);official_005["i"][8][1]="too short"
        repaired=compact_protocol.canonicalize_native(official_005,derived_compact)
        self.assertEqual("S"*16,repaired["i"][8][1])
        mixed=deepcopy(derived_base);mixed["i"].pop(0);mixed["i"][0][1]="not applicable"
        repaired=compact_protocol.canonicalize_native(mixed,derived_compact)
        self.assertEqual(28,len(repaired["i"]))
        excessive=deepcopy(derived_base);excessive["i"].pop(0)
        excessive["i"][0][1]="too short";excessive["i"][1][1]="not applicable"
        with self.assertRaisesRegex(compact_protocol.CompactProtocolError,"derived item limit"):
            compact_protocol.canonicalize_native(excessive,derived_compact)

        base={"v":1,"i":[[row[0],"E"*16] for row in compact["i"]],
              "a":[[ordinal,"A","S"*16] for ordinal in action_ordinals],"c":[]}
        mutations=(
            ("missing-action",lambda value:value["a"].pop()),
            ("duplicate-action",lambda value:value["a"].append(deepcopy(value["a"][0]))),
            ("unknown-action",lambda value:value["a"][0].__setitem__(0,999)),
            ("typed-action",lambda value:value["a"][0].__setitem__(0,True)),
            ("generic-action",lambda value:value["a"][0].__setitem__(2,"not applicable")),
            ("short-action",lambda value:value["a"][0].__setitem__(2,"too short")),
            ("unbroken-overflow",lambda value:value["a"][0].__setitem__(2,"x"*33)),
            ("duplicate-item",lambda value:value["i"].append(deepcopy(value["i"][0]))),
            ("unknown-item",lambda value:value["i"][0].__setitem__(0,999)),
            ("typed-item",lambda value:value["i"][0].__setitem__(0,"0")),
            ("non-string-item",lambda value:value["i"][0].__setitem__(1,["invalid"])),
            ("short-claim",lambda value:value["c"].append(
                ["C","f","P1",action_ordinals[0],"short","bounded control","bounded oracle"])),
            ("generic-claim",lambda value:value["c"].append(
                ["C","f","P1",action_ordinals[0],"not applicable","bounded control","bounded oracle"])),
        )
        for name,mutate in mutations:
            value=deepcopy(base);mutate(value)
            with self.subTest(invalid=name):
                with self.assertRaises(compact_protocol.CompactProtocolError):
                    compact_protocol.canonicalize_native(value,compact)

    def test_candidate_static_binds_mapping_selection_actions_ranges_and_fragment(self):
        candidate,expected_mapping=_static_candidate_fixture()
        compact_protocol.validate_candidate_static(candidate,expected_ordinal_map=expected_mapping)

        def rehash_mapping(value):
            value["ordinal_map_sha256"]=compact_protocol.digest(value["ordinal_map"])

        def rehash_compact(value):
            value["compact_context_sha256"]=compact_protocol.digest(value["compact_context"])

        def drift_source_line(value):
            value["compact_context"]["s"][1][2]=1
            value["compact_context"]["s"][1][4]="preceding\nsecond\nthird"

        mutations=(
            ("map-version",lambda value:value["ordinal_map"].update(version="other"),rehash_mapping),
            ("map-shape",lambda value:value["ordinal_map"].update(extra=True),rehash_mapping),
            ("item-ordinal",lambda value:value["ordinal_map"]["items"][1].update(ordinal=3),rehash_mapping),
            ("action-ordinal",lambda value:value["ordinal_map"]["actions"][1].update(ordinal=3),rehash_mapping),
            ("source-line",drift_source_line,rehash_compact),
            ("item-order",lambda value:value["compact_context"]["i"].reverse(),rehash_compact),
            ("action-order",lambda value:value["compact_context"]["i"][0][1].reverse(),rehash_compact),
            ("action-missing",lambda value:value["compact_context"]["i"][0][1].pop(),rehash_compact),
            ("action-extra",lambda value:value["compact_context"]["i"][0][1].append([99,"extra"]),rehash_compact),
            ("fragment",lambda value:value["compact_context"].update(f="frag-drift"),rehash_compact),
            ("range-split",lambda value:value["context_pack"].update(allowed_ranges=[
                {"inventory_ids":["INV-0000000000000001"],"path":"a.c","line_start":1,"line_end":1},
                {"inventory_ids":["INV-0000000000000002"],"path":"a.c","line_start":2,"line_end":3},
                value["context_pack"]["allowed_ranges"][1],
            ]),None),
        )
        for name,mutate,rehash in mutations:
            value=deepcopy(candidate);mutate(value)
            if rehash is not None: rehash(value)
            with self.subTest(drift=name):
                with self.assertRaises(compact_protocol.CompactProtocolError):
                    compact_protocol.validate_candidate_static(value,expected_ordinal_map=expected_mapping)

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

    def test_worker_native_and_context_require_integer_compact_version_one(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory,ledger,snapshot=_equivalent_fixture(Path(temp),1)
            _,contexts=compact_protocol.capacity_plan(inventory,ledger,snapshot,"run-fixed")
            mapping=compact_protocol.ordinal_map(inventory,ledger);planned=contexts[0]
            native=compact_protocol.maximum_native_output(planned["compact_context"],mapping)
            action_map={row["ordinal"]:row for row in mapping["actions"]}
            pack={"run_id":"run-fixed","fragment_id":planned["fragment_id"],"repository":"driver",
                  "commit":"a"*40,"obligation_ids":[action_map[x]["obligation_id"] for x in planned["action_ordinals"]],
                  "skill_receipts":[]}
            self.assertIsInstance(compact_protocol.expand_native(
                native,planned["compact_context"],mapping,pack),dict)
            for invalid in (True,False,1.0,"1",None):
                with self.subTest(boundary="worker-native",version=invalid):
                    changed=deepcopy(native);changed["v"]=invalid
                    with self.assertRaisesRegex(compact_protocol.CompactProtocolError,"native output closure"):
                        compact_protocol.expand_native(changed,planned["compact_context"],mapping,pack)
                with self.subTest(boundary="compact-context",version=invalid):
                    changed=deepcopy(planned["compact_context"]);changed["v"]=invalid
                    with self.assertRaisesRegex(compact_protocol.CompactProtocolError,"context version"):
                        compact_protocol.expand_native(native,changed,mapping,pack)

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
