from __future__ import annotations

import builtins
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from ugcam_ai.adapters.nx_flow import NxFlowMapper
from ugcam_ai.adapters.nx_journal import (
    NxJournalAdapter,
    NxJournalLimits,
    NxJournalResourceLimitError,
    NxUnsupportedSourceError,
)
from ugcam_ai.versioning import NxVersionContract


def _nodes(graph):
    return [node for flow in graph["flows"] for node in flow["nodes"]]


def _edges(graph):
    return [edge for flow in graph["flows"] for edge in flow["edges"]]


class NxFlowImportTest(unittest.TestCase):
    def test_outputs_frozen_flowgraph_shape_and_exact_source_mappings(self) -> None:
        source = PLUGIN_ROOT / "examples" / "nx_flow" / "review_only_journal.py"
        raw = source.read_bytes()

        graph = NxFlowMapper().map_path(source)

        self.assertEqual(graph["schema_version"], 1)
        self.assertEqual(graph["contract"], "cam.flowgraph.v1")
        self.assertEqual(graph["product"], "nx")
        self.assertEqual(graph["entry_flow_id"], "flow:main")
        self.assertEqual({flow["kind"] for flow in graph["flows"]}, {"main", "subflow"})
        self.assertEqual(len(graph["source_mappings"]), len(_nodes(graph)))
        self.assertRegex(graph["semantic_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(graph["source_snapshot_hash"], r"^sha256:[0-9a-f]{64}$")

        node_fields = {
            "node_id",
            "node_type",
            "node_type_version",
            "enabled",
            "risk",
            "review_status",
            "port_contract_refs",
            "bindings",
            "configuration",
            "source_mapping_ids",
            "fidelity",
            "capability_ref",
            "compatibility_status",
            "opaque",
            "extensions",
        }
        for node in _nodes(graph):
            self.assertTrue(node_fields.issubset(node))
            self.assertRegex(node["node_type"], r"^(?:cam|nx|flow|opaque)\.")
            self.assertIn(node["risk"], {"safe", "review", "blocked"})
            self.assertIn(
                node["review_status"],
                {"unreviewed", "needs_review", "accepted", "rejected"},
            )
            if node["opaque"] is not None:
                self.assertTrue(
                    {
                        "reason",
                        "asset_revision_id",
                        "source_span_ids",
                        "content_hash",
                        "round_trip_policy",
                        "semantic_editable",
                        "diagnostic_codes",
                    }.issubset(node["opaque"])
                )
        for edge in _edges(graph):
            self.assertIn(edge["kind"], {"control", "data", "dependency"})
            self.assertEqual(
                set(edge),
                {
                    "edge_id",
                    "kind",
                    "source",
                    "target",
                    "condition",
                    "priority",
                    "source_mapping_ids",
                    "extensions",
                },
            )

        for mapping in graph["source_mappings"]:
            span = mapping["source_span"]
            self.assertEqual(mapping["source_line"], span["start_line"])
            self.assertLessEqual(span["start_byte"], span["end_byte"])
            excerpt = raw[span["start_byte"] : span["end_byte"]]
            self.assertEqual(
                mapping["excerpt_hash"],
                "sha256:" + hashlib.sha256(excerpt).hexdigest(),
            )
            self.assertEqual(span["column_encoding"], "unicode_scalar")

        safety = graph["extensions"]["nx"]["safety"]
        self.assertEqual(safety["transport"], "none")
        self.assertEqual(safety["commands_sent"], 0)
        self.assertFalse(safety["journal_imported"])
        self.assertFalse(safety["journal_executed"])
        self.assertEqual(safety["machine_output_count"], 0)

    def test_maps_functions_branches_loops_and_exception_edges_without_evaluation(
        self,
    ) -> None:
        source = """
import NXOpen

def helper(session, values):
    for value in values:
        if value:
            session.SetUndoMarkName(1, "x")
        else:
            pass

def main():
    session = NXOpen.Session.GetSession()
    try:
        helper(session, [0, 1])
    except NXOpen.NXException:
        raise
    finally:
        session.SetUndoMarkName(1, "done")

if __name__ == "__main__":
    main()
"""
        graph = NxFlowMapper().map_source(source)

        self.assertEqual(
            {flow["flow_id"] for flow in graph["flows"]},
            {"flow:main", "flow:function:helper", "flow:function:main"},
        )
        node_types = {node["node_type"] for node in _nodes(graph)}
        self.assertTrue(
            {
                "flow.function.define",
                "flow.subflow_call",
                "flow.if",
                "flow.loop",
                "flow.try",
                "flow.except",
                "flow.finally",
            }.issubset(node_types)
        )
        roles = {edge["extensions"]["nx"]["edge_role"] for edge in _edges(graph)}
        self.assertTrue(
            {
                "true",
                "false",
                "loop_body",
                "loop_back",
                "loop_exit",
                "exception",
                "finally",
            }.issubset(roles)
        )

    def test_unicode_columns_and_utf8_byte_spans_remain_exact(self) -> None:
        source = (
            'import NXOpen\nlabel = "刀具"; '
            "session = NXOpen.Session.GetSession()\n"
        )
        graph = NxFlowMapper().map_source(source)
        session_node = next(
            node for node in _nodes(graph) if node["node_type"] == "nx.session.access"
        )
        mapping = next(
            item
            for item in graph["source_mappings"]
            if item["mapping_id"] == session_node["source_mapping_ids"][0]
        )
        span = mapping["source_span"]
        raw = source.encode("utf-8")
        excerpt = raw[span["start_byte"] : span["end_byte"]].decode("utf-8")

        self.assertEqual(excerpt, "NXOpen.Session.GetSession()")
        self.assertEqual(
            span["start_column"],
            source.splitlines()[1].index("NXOpen.Session"),
        )

    def test_builder_complete_abandon_and_invalid_lifecycles_are_first_class(
        self,
    ) -> None:
        complete = """
import NXOpen
collection = NXOpen.Session.GetSession().Parts.Work.CAMSetup.CAMOperationCollection
builder = collection.CreatePlanarMillingBuilder(None)
builder.SetTolerance(0.02)
builder.Commit()
builder.Destroy()
"""
        complete_graph = NxFlowMapper().map_source(complete)
        complete_types = [node["node_type"] for node in _nodes(complete_graph)]
        self.assertTrue(
            {
                "cam.builder.create",
                "cam.builder.configure",
                "cam.builder.commit",
                "cam.builder.destroy",
            }.issubset(complete_types)
        )
        self.assertNotIn("nx.builder.lifecycle_incomplete", complete_types)
        dependency_roles = {
            edge["extensions"]["nx"]["edge_role"]
            for edge in _edges(complete_graph)
            if edge["kind"] == "dependency"
        }
        self.assertTrue({"configures", "commits", "destroys"}.issubset(dependency_roles))

        abandoned = """
import NXOpen
collection = NXOpen.Session.GetSession().Parts.Work.CAMSetup.CAMOperationCollection
builder = collection.CreatePlanarMillingBuilder(None)
builder.Destroy()
"""
        abandoned_graph = NxFlowMapper().map_source(abandoned)
        abandoned_types = [node["node_type"] for node in _nodes(abandoned_graph)]
        self.assertIn("cam.builder.abandon", abandoned_types)
        self.assertIn("cam.builder.destroy", abandoned_types)
        self.assertNotIn("nx.builder.lifecycle_incomplete", abandoned_types)

        invalid = """
import NXOpen
collection = NXOpen.Session.GetSession().Parts.Work.CAMSetup.CAMOperationCollection
builder = collection.CreatePlanarMillingBuilder(None)
builder.Commit()
builder.Commit()
builder.Destroy()
builder.SetTolerance(0.03)
"""
        invalid_graph = NxFlowMapper().map_source(invalid)
        lifecycle = next(
            node
            for node in _nodes(invalid_graph)
            if node["node_type"] == "nx.builder.lifecycle_incomplete"
        )
        issues = lifecycle["configuration"]["issues"]
        self.assertIn("builder_committed_more_than_once", issues)
        self.assertIn("builder_used_after_destroy", issues)
        self.assertEqual(lifecycle["risk"], "blocked")
        self.assertEqual(lifecycle["review_status"], "rejected")

    def test_conditional_builder_transition_is_not_claimed_complete(self) -> None:
        source = """
import NXOpen
collection = NXOpen.Session.GetSession().Parts.Work.CAMSetup.CAMOperationCollection
builder = collection.CreatePlanarMillingBuilder(None)
if condition:
    builder.Commit()
builder.Destroy()
"""
        graph = NxFlowMapper().map_source(source)
        lifecycle = next(
            node
            for node in _nodes(graph)
            if node["node_type"] == "nx.builder.lifecycle_incomplete"
        )

        self.assertIn(
            "builder_lifecycle_is_path_dependent",
            lifecycle["configuration"]["issues"],
        )

    def test_undo_scope_guards_mutations_and_incomplete_scope_is_blocked(self) -> None:
        complete = """
import NXOpen
session = NXOpen.Session.GetSession()
mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Visible, "builder")
builder = session.Parts.Work.CAMSetup.CAMOperationCollection.CreatePlanarMillingBuilder(None)
builder.Commit()
builder.Destroy()
session.DeleteUndoMark(mark, False)
"""
        graph = NxFlowMapper().map_source(complete)
        types = [node["node_type"] for node in _nodes(graph)]
        self.assertIn("nx.undo.scope.open", types)
        self.assertIn("nx.undo.scope.close", types)
        self.assertNotIn("nx.undo.scope_incomplete", types)
        roles = {
            edge["extensions"]["nx"]["edge_role"]
            for edge in _edges(graph)
            if edge["kind"] == "dependency"
        }
        self.assertIn("guards", roles)

        incomplete = """
import NXOpen
session = NXOpen.Session.GetSession()
mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Visible, "left open")
"""
        blocked = NxFlowMapper().map_source(incomplete)
        self.assertIn(
            "nx.undo.scope_incomplete",
            [node["node_type"] for node in _nodes(blocked)],
        )

    def test_selector_zero_and_many_matches_never_choose_an_object(self) -> None:
        source = """
import NXOpen
setup = NXOpen.Session.GetSession().Parts.Work.CAMSetup
missing = setup.CAMGroupCollection.FindObject("MISSING")
many = setup.CAMGroupCollection.FindObject("MANY")
journal_id = many.JournalIdentifier
"""
        graph = NxFlowMapper().map_source(
            source,
            selector_match_counts={"MISSING": 0, "MANY": 3},
        )
        selectors = graph["extensions"]["nx"]["selectors"]
        by_identifier = {
            selector["recorded_identifier"]: selector
            for selector in selectors
            if selector["recorded_identifier"] is not None
        }

        self.assertEqual(by_identifier["MISSING"]["resolution_status"], "missing")
        self.assertEqual(by_identifier["MANY"]["resolution_status"], "ambiguous")
        self.assertIsNone(by_identifier["MISSING"]["selected_object"])
        self.assertIsNone(by_identifier["MANY"]["selected_object"])
        self.assertFalse(by_identifier["MISSING"]["unique_match_claimed"])
        self.assertFalse(by_identifier["MANY"]["unique_match_claimed"])
        journal_selector = next(
            item for item in selectors if item["source"] == "JournalIdentifier"
        )
        self.assertTrue(journal_selector["fragile"])
        self.assertFalse(journal_selector["resolved"])
        self.assertEqual(journal_selector["resolution_status"], "unresolved")
        selector_nodes = [
            node for node in _nodes(graph) if node["node_type"].startswith("nx.selector.")
        ]
        self.assertTrue(all(node["risk"] == "blocked" for node in selector_nodes))
        self.assertTrue(all(node["review_status"] == "rejected" for node in selector_nodes))

    def test_target_stubs_are_evidence_and_never_a_capability_claim(self) -> None:
        contract = NxVersionContract.load_stubs(
            PLUGIN_ROOT / "examples" / "nx_stubs" / "NX2406",
            expected_version="NX 2406",
        )
        graph = NxFlowMapper().map_source(
            "import NXOpen\nsession = NXOpen.Session.GetSession()\n",
            target_contract=contract,
        )
        evidence = graph["extensions"]["nx"]["api_symbol_evidence"]
        session = next(
            item for item in evidence if item["api_symbol"] == "NXOpen.Session.GetSession"
        )

        self.assertEqual(graph["target_versions"], ["NX 2406"])
        self.assertEqual(session["status"], "observed_in_target_stubs")
        self.assertFalse(session["capability_claimed"])
        target = graph["extensions"]["nx"]["target_api_contract_evidence"]
        self.assertEqual(target["status"], "static_stub_evidence_only")
        self.assertFalse(target["capability_claimed"])
        self.assertFalse(target["uf_coverage_complete"])

        uf_graph = NxFlowMapper().map_source(
            "import NXOpen\nuf = NXOpen.UF.UFSession.GetUFSession()\n",
            target_contract=contract,
        )
        uf_node = next(
            node
            for node in _nodes(uf_graph)
            if node["configuration"].get("api_symbol")
            == "NXOpen.UF.UFSession.GetUFSession"
        )
        self.assertEqual(uf_node["risk"], "blocked")
        self.assertEqual(
            uf_node["configuration"]["api_evidence"]["coverage_gap"],
            "uf_wrapped_method_not_proven",
        )

    def test_unsupported_keeps_text_digest_span_but_safety_payload_is_redacted(
        self,
    ) -> None:
        unsupported = """
import NXOpen
session = NXOpen.Session.GetSession()
dynamic = getattr(session, method_name)
"""
        graph = NxFlowMapper().map_source(unsupported)
        region = graph["extensions"]["nx"]["unsupported_regions"][0]
        self.assertIn("getattr", region["original_text"])
        self.assertRegex(region["source_excerpt_hash"], r"^sha256:[0-9a-f]{64}$")
        self.assertTrue(region["blocker"])
        self.assertIn("start_byte", region["source_span"])

        forbidden = """
import NXOpen
session = NXOpen.Session.GetSession()
session.CAMSetup.GenerateGCode("SECRET_MACHINE_PAYLOAD")
"""
        blocked = NxFlowMapper().map_source(forbidden)
        serialized = json.dumps(blocked, sort_keys=True)
        self.assertNotIn("SECRET_MACHINE_PAYLOAD", serialized)
        safety_region = blocked["extensions"]["nx"]["unsupported_regions"][0]
        self.assertTrue(safety_region["payload_redacted"])
        self.assertIsNone(safety_region["original_text"])
        self.assertIn(
            "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
            {
                item["code"]
                for item in blocked["extensions"]["nx"]["diagnostics"]
            },
        )
        safety_node = next(
            node for node in _nodes(blocked) if node["node_type"].startswith("opaque.")
        )
        self.assertIn(
            "SAFETY_MACHINE_OUTPUT_FORBIDDEN",
            safety_node["opaque"]["diagnostic_codes"],
        )

        path_graph = NxFlowMapper().map_source(
            'import NXOpen\npath = "C:/private/project/part.prt"\n'
        )
        self.assertNotIn(
            "C:/private/project/part.prt",
            json.dumps(path_graph, sort_keys=True),
        )

    def test_malicious_journal_is_never_imported_executed_or_replayed(self) -> None:
        source = """
import NXOpen
raise RuntimeError("would execute")
__import__("os").system("STATIC_ANALYSIS_SENTINEL")
"""
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "NXOpen":
                raise AssertionError("Journal import attempted")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=guarded_import) as importer,
            mock.patch("builtins.exec") as execute,
            mock.patch("builtins.eval") as evaluate,
            mock.patch.object(os, "system") as system,
        ):
            graph = NxFlowMapper().map_source(source)

        self.assertFalse(any(call.args[0] == "NXOpen" for call in importer.call_args_list))
        execute.assert_not_called()
        evaluate.assert_not_called()
        system.assert_not_called()
        safety = graph["extensions"]["nx"]["safety"]
        self.assertFalse(safety["journal_imported"])
        self.assertFalse(safety["journal_executed"])
        self.assertFalse(safety["journal_replayed"])

    def test_resource_limits_fail_closed_without_partial_graph(self) -> None:
        byte_limited = NxFlowMapper(
            journal_adapter=NxJournalAdapter(
                limits=NxJournalLimits(max_source_bytes=32)
            )
        )
        with self.assertRaises(NxJournalResourceLimitError):
            byte_limited.map_source("import NXOpen\n" + (" " * 64))

        node_limited = NxFlowMapper(
            journal_adapter=NxJournalAdapter(
                limits=NxJournalLimits(max_ast_nodes=12)
            )
        )
        with self.assertRaises(NxJournalResourceLimitError):
            node_limited.map_source(
                "import NXOpen\nvalues = [1, 2, 3, 4, 5, 6, 7, 8]\n"
            )

        depth_limited = NxFlowMapper(
            journal_adapter=NxJournalAdapter(
                limits=NxJournalLimits(max_ast_depth=24)
            )
        )
        deeply_nested = "import NXOpen\nvalue = " + ("[" * 40) + "0" + ("]" * 40)
        with self.assertRaises(NxJournalResourceLimitError):
            depth_limited.map_source(deeply_nested)

    def test_power_mill_and_generic_python_are_rejected(self) -> None:
        with self.assertRaises(NxUnsupportedSourceError):
            NxFlowMapper().map_source(
                "event = {'product': 'powermill', 'command': 'CALCULATE'}\n"
            )
        with self.assertRaises(NxUnsupportedSourceError):
            NxFlowMapper().map_source(
                "import NXOpen\nproduct = 'powermill'\n"
            )

    def test_hash_and_non_parameter_source_regions_are_deterministic(self) -> None:
        template = """
import NXOpen
session = NXOpen.Session.GetSession()
builder = session.Parts.Work.CAMSetup.CAMOperationCollection.CreatePlanarMillingBuilder(None)
builder.Tolerance = VALUE
builder.Commit()
builder.Destroy()
"""
        first = NxFlowMapper().map_source(template.replace("VALUE", "0.02"))
        repeat = NxFlowMapper().map_source(template.replace("VALUE", "0.02"))
        changed = NxFlowMapper().map_source(template.replace("VALUE", "0.03"))

        self.assertEqual(first["semantic_hash"], repeat["semantic_hash"])
        self.assertEqual(
            json.dumps(first, sort_keys=True),
            json.dumps(repeat, sort_keys=True),
        )
        self.assertNotEqual(first["semantic_hash"], changed["semantic_hash"])

        def mapping_for(graph, node_type):
            node = next(item for item in _nodes(graph) if item["node_type"] == node_type)
            mapping_id = node["source_mapping_ids"][0]
            return next(
                item
                for item in graph["source_mappings"]
                if item["mapping_id"] == mapping_id
            )

        self.assertEqual(
            mapping_for(first, "nx.session.access")["excerpt_hash"],
            mapping_for(changed, "nx.session.access")["excerpt_hash"],
        )
        self.assertNotEqual(
            mapping_for(first, "cam.builder.configure")["excerpt_hash"],
            mapping_for(changed, "cam.builder.configure")["excerpt_hash"],
        )


if __name__ == "__main__":
    unittest.main()
