"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..", "..");
const core = require(path.join(root, "cam_automation", "web", "flow", "flow-core.js"));
const fixtures = require(path.join(root, "cam_automation", "web", "flow", "fixtures.js"));

function bundle() {
  return fixtures.makeBundle();
}

test("source line and span mappings remain bidirectional and stably ordered", () => {
  const data = bundle();
  for (const node of data.graph.flows[0].nodes) {
    const mappings = core.sourceMappingsForNode(data.graph, node.node_id);
    for (const mapping of mappings) {
      assert.equal(mapping.source_line, mapping.source_span.start_line);
      const reverse = core.nodesForSourceLine(
        data.graph,
        mapping.asset_revision_id,
        mapping.source_line
      );
      assert.ok(reverse.some((candidate) =>
        candidate.node_id === node.node_id
        && candidate.mapping_id === mapping.mapping_id
      ));
    }
  }
});

test("ambiguous reverse mappings return every candidate and never guess one", () => {
  const data = bundle();
  const graph = core.clone(data.graph);
  const first = core.clone(graph.source_mappings[0]);
  const second = core.clone(first);
  first.mapping_id = "mapping:t10:ambiguous:a";
  first.mapping_quality = "ambiguous";
  first.target.node_id = graph.flows[0].nodes[1].node_id;
  second.mapping_id = "mapping:t10:ambiguous:b";
  second.mapping_quality = "ambiguous";
  second.target.node_id = graph.flows[0].nodes[2].node_id;
  graph.source_mappings.push(first, second);

  const candidates = core.nodesForSourceLine(
    graph,
    first.asset_revision_id,
    first.source_line
  ).filter((item) => item.mapping_id.startsWith("mapping:t10:ambiguous:"));
  assert.deepEqual(
    candidates.map((item) => item.mapping_id),
    ["mapping:t10:ambiguous:a", "mapping:t10:ambiguous:b"]
  );
  assert.equal(new Set(candidates.map((item) => item.node_id)).size, 2);
});

test("unknown optional fields survive semantic edit undo and redo", () => {
  const data = bundle();
  const graph = core.clone(data.graph);
  graph.x_t10_unknown_optional = { preserve: true, opaque_value: "future-v1" };
  graph.extensions["t10.unknown"] = {
    semantic: false,
    nested: { preserve: ["exactly", 1, true] }
  };
  const store = new core.GraphStore(graph);
  const before = store.canonicalJson();
  store.execute(core.updateConfigurationCommand("node:tolerance", "value", 0.075));
  assert.deepEqual(
    store.graph.x_t10_unknown_optional,
    graph.x_t10_unknown_optional
  );
  assert.deepEqual(store.graph.extensions["t10.unknown"], graph.extensions["t10.unknown"]);
  store.undo();
  assert.equal(store.canonicalJson(), before);
  store.redo();
  assert.deepEqual(
    store.graph.x_t10_unknown_optional,
    graph.x_t10_unknown_optional
  );
});
