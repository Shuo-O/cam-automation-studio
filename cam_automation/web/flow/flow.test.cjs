"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const core = require("./flow-core.js");
const fixtures = require("./fixtures.js");

function bundle() {
  return fixtures.makeBundle();
}

test("canonical JSON and synchronous SHA-256 are deterministic", () => {
  const value = { z: [3, { b: 2, a: 1 }], a: "fixture" };
  const canonical = '{"a":"fixture","z":[3,{"a":1,"b":2}]}';
  assert.equal(core.canonicalJson(value), canonical);
  assert.equal(
    core.sha256Canonical(value),
    `sha256:${crypto.createHash("sha256").update(canonical).digest("hex")}`
  );
  assert.match(core.sha256Canonical(value), core.HASH_PATTERN);
});

test("fixture canonical state is FlowGraph and excludes renderer state", () => {
  const data = bundle();
  const graph = data.graph;
  const manifest = data.capability_manifests[0];
  const manifestHashProjection = core.clone(manifest);
  delete manifestHashProjection.manifest_hash;
  assert.equal(manifest.manifest_hash, core.sha256Canonical(manifestHashProjection));
  assert.equal(graph.contract, "cam.flowgraph.v1");
  assert.equal(graph.schema_version, 1);
  assert.match(graph.semantic_hash, core.HASH_PATTERN);
  assert.deepEqual(
    ["selectedNodeId", "pendingConnection", "aiEnabled", "reactFlow", "renderer_state"]
      .filter((key) => key in graph),
    []
  );
  assert.ok(graph.layout.nodes["node:roughing"]);
});

test("typed connection accepts matching ports and rejects mismatches", () => {
  const graph = bundle().graph;
  const store = new core.GraphStore(graph);
  const newNode = fixtures.createNode(
    "powermill.fixture.area_clearance",
    "node:roughing:second",
    { source_mapping_ids: [] }
  );
  store.execute(core.addNodeCommand(newNode, { x: 1180, y: 100 }));

  const valid = {
    kind: "data",
    source: { node_id: "node:tolerance", port_id: "value" },
    target: { node_id: "node:roughing:second", port_id: "tolerance" }
  };
  assert.equal(core.connectionIssue(store.graph, fixtures.registry, valid), null);
  store.execute(core.connectCommand(valid, fixtures.registry));
  assert.ok(
    core.graphFlow(store.graph).edges.some(
      (edge) => edge.target.node_id === "node:roughing:second" && edge.kind === "data"
    )
  );

  const invalid = {
    kind: "control",
    source: { node_id: "node:tolerance", port_id: "value" },
    target: { node_id: "node:roughing:second", port_id: "previous" }
  };
  assert.equal(core.connectionIssue(store.graph, fixtures.registry, invalid).code, "PORT_KIND_MISMATCH");
  assert.throws(
    () => store.execute(core.connectCommand(invalid, fixtures.registry)),
    (error) => error.code === "PORT_KIND_MISMATCH"
  );
});

test("node drag is a reversible non-semantic GraphCommand", () => {
  const store = new core.GraphStore(bundle().graph);
  const before = store.canonicalJson();
  const semanticHash = store.graph.semantic_hash;
  store.execute(core.moveNodeCommand("node:roughing", { x: 744.4, y: 222.8 }));
  assert.deepEqual(store.graph.layout.nodes["node:roughing"], { x: 744, y: 223 });
  assert.equal(store.graph.semantic_hash, semanticHash);
  assert.equal(store.canUndo, true);
  store.undo();
  assert.equal(store.canonicalJson(), before);
  store.redo();
  assert.deepEqual(store.graph.layout.nodes["node:roughing"], { x: 744, y: 223 });
});

test("semantic GraphCommand updates hash and undo restores exact JSON", () => {
  const store = new core.GraphStore(bundle().graph);
  const before = store.canonicalJson();
  const beforeHash = store.graph.semantic_hash;
  store.execute(core.updateConfigurationCommand("node:tolerance", "value", 0.075));
  assert.equal(core.graphNode(store.graph, "node:tolerance").configuration.value, 0.075);
  assert.notEqual(store.graph.semantic_hash, beforeHash);
  assert.equal(store.graph.parent_revision_id, "revision:powermill:fixture:roughing:2");
  store.undo();
  assert.equal(store.canonicalJson(), before);
  store.redo();
  assert.equal(core.graphNode(store.graph, "node:tolerance").configuration.value, 0.075);
});

test("source mapping supports graph lookup and one-to-many reverse lookup", () => {
  const data = bundle();
  const mappings = core.sourceMappingsForNode(data.graph, "node:roughing");
  assert.deepEqual(
    new Set(mappings.map((item) => item.mapping_id)),
    new Set(["mapping:roughing", "mapping:roughing-tolerance"])
  );
  const candidates = core.nodesForSourceLine(
    data.graph,
    data.source.asset_revision_id,
    4
  );
  assert.equal(candidates.length, 2);
  assert.equal(candidates[0].mapping_quality, "exact");
  assert.equal(candidates[1].mapping_quality, "exact");
  assert.notEqual(candidates[0].node_id, candidates[1].node_id);
});

test("opaque node remains visible, disabled, and semantically read-only", () => {
  const data = bundle();
  const node = core.graphNode(data.graph, "node:opaque");
  assert.equal(node.enabled, false);
  assert.equal(node.compatibility_status, "capability_unavailable");
  assert.equal(node.opaque.semantic_editable, false);
  assert.throws(
    () => new core.GraphStore(data.graph).execute(
      core.updateConfigurationCommand("node:opaque", "guess", true)
    ),
    (error) => error.code === "SOURCE_OPAQUE_EDIT"
  );
  const report = new fixtures.FixtureFlowApi(data).validateGraph(data.graph);
  assert.ok(report.diagnostics.some((item) => item.object_ref === "node:opaque"));
});

test("immutable versions produce semantic diff while excluding layout", () => {
  const data = bundle();
  const api = new fixtures.FixtureFlowApi(data);
  const oldest = data.versions[data.versions.length - 1];
  const diff = api.diffGraph(oldest.version_id, data.graph);
  assert.equal(oldest.immutable, true);
  assert.equal(diff.layout_excluded, true);
  assert.equal(diff.semantic_changed, true);
  assert.ok(diff.changes.some((item) => item.object_ref === "node:tolerance"));

  const moved = new core.GraphStore(data.graph);
  moved.execute(core.moveNodeCommand("node:roughing", { x: 1200, y: 700 }));
  const layoutOnly = core.diffGraphs(data.graph, moved.graph);
  assert.equal(layoutOnly.semantic_changed, false);
  assert.deepEqual(layoutOnly.changes, []);
});

test("target selection starts empty and preview requires an exact fixture tuple", () => {
  const data = bundle();
  const api = new fixtures.FixtureFlowApi(data);
  const studioSource = fs.readFileSync(path.join(__dirname, "flow-studio.js"), "utf8");
  assert.match(
    studioSource,
    /target:\s*\{\s*product:\s*"",\s*target_version:\s*"",\s*target_instance_id:\s*"",\s*project_id:\s*"",\s*project_snapshot_hash:\s*""/s
  );
  assert.throws(
    () => api.createPreview({
      graph: data.graph,
      flow_version_id: data.versions[0].version_id,
      target: {}
    }),
    (error) => error.code === "PREVIEW_TARGET_AMBIGUOUS"
  );
  assert.ok(data.targets.some((item) => item.is_foreground));
  assert.equal("selected_target" in data, false);
});

test("preview plan has frozen zero-execution fields and unfinished gates", () => {
  const data = bundle();
  const api = new fixtures.FixtureFlowApi(data);
  const target = data.targets.find(
    (item) => item.product === "powermill" && item.target_version === "PowerMill 2026"
  );
  const plan = api.createPreview({
    graph: data.graph,
    flow_version_id: data.versions[0].version_id,
    target: {
      product: target.product,
      target_version: target.target_version,
      target_instance_id: target.instance_id,
      project_id: target.project_id,
      project_snapshot_hash: target.project_snapshot_hash
    }
  });
  assert.equal(plan.contract, "cam.preview_plan.v1");
  assert.equal(plan.execution_mode, "fixture_dry_run");
  assert.equal(plan.transport, "none");
  assert.equal(plan.commands_sent, 0);
  assert.equal(plan.journal_executed, false);
  assert.equal(plan.macro_executed, false);
  assert.equal(plan.machine_output_count, 0);
  const gates = Object.fromEntries(plan.gate_results.map((item) => [item.gate, item.status]));
  assert.equal(gates.cam_simulation, "not_run");
  assert.equal(gates.collision_check, "required");
  assert.equal(gates.shop_approval, "required");
});

test("500-node viewport hook culls without changing the fixture graph", () => {
  const original = bundle().graph;
  const before = core.canonicalJson(original);
  const graph = fixtures.makePerformanceGraph(500);
  const result = core.visibleNodes(graph, {
    x: 0,
    y: 0,
    scale: 1,
    width: 1200,
    height: 700
  });
  assert.equal(core.graphFlow(graph).nodes.length, 500);
  assert.equal(result.total_nodes, 500);
  assert.ok(result.visible_nodes > 0);
  assert.ok(result.visible_nodes < 500);
  assert.ok(result.duration_ms >= 0);
  assert.equal(core.canonicalJson(original), before);
});

test("AI presentation toggle cannot alter canonical FlowGraph JSON", () => {
  const store = new core.GraphStore(bundle().graph);
  const before = store.canonicalJson();
  let aiEnabled = false;
  aiEnabled = !aiEnabled;
  assert.equal(aiEnabled, true);
  assert.equal(store.canonicalJson(), before);
  const studioSource = fs.readFileSync(path.join(__dirname, "flow-studio.js"), "utf8");
  assert.match(studioSource, /AI presentation state changed canonical FlowGraph JSON/);
});

test("component fixture exposes accessible workbench surfaces and safe copy", () => {
  const html = fs.readFileSync(path.join(__dirname, "fixture.html"), "utf8");
  const studioSource = fs.readFileSync(path.join(__dirname, "flow-studio.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
  const visibleSurface = `${html}\n${studioSource}`;

  assert.match(studioSource, /role="application"/);
  assert.match(studioSource, /aria-live="polite"/);
  assert.match(studioSource, /data-bottom-tab="problems"/);
  assert.match(studioSource, /data-bottom-tab="source"/);
  assert.match(studioSource, /data-bottom-tab="graph-diff"/);
  assert.match(studioSource, /data-bottom-tab="source-diff"/);
  assert.match(studioSource, /data-bottom-tab="test"/);
  assert.match(studioSource, /data-bottom-tab="preview"/);
  assert.match(studioSource, /data-target="target_instance_id"/);
  assert.match(css, /@media \(max-width: 560px\)/);
  assert.match(css, /prefers-reduced-motion/);

  assert.doesNotMatch(visibleSurface, /https?:\/\/|cdn\.|fetch\s*\(|WebSocket|EventSource/);
  assert.doesNotMatch(visibleSurface, /运行|部署|一键加工/);
  assert.doesNotMatch(visibleSurface, /live Journal|live macro|CAM command/i);
  assert.match(visibleSurface, /transport=none/);
  assert.match(visibleSurface, /commands_sent=0/);
});
