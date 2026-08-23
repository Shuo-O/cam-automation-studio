"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const fixture = require("./fixtures.js");

test("workbench fixture covers the frozen event contract at pagination scale", () => {
  assert.equal(fixture.schema_version, 1);
  assert.equal(fixture.events.length, 612);
  assert.equal(new Set(fixture.events.map((event) => event.event_id)).size, 612);
  assert.deepEqual(
    new Set(fixture.events.map((event) => event.source_mode)),
    new Set(["manual", "automation", "system", "execution_audit"])
  );
  assert.deepEqual(
    new Set(fixture.events.map((event) => event.view_level)),
    new Set(["L0", "L1", "L2", "L3", "L4"])
  );
  fixture.events
    .filter((event) => event.source_mode === "execution_audit")
    .forEach((event) => assert.equal(event.mode, "automation"));
});

test("instance rows expose every connection drawer field", () => {
  const required = [
    "product",
    "pid",
    "window_handle",
    "project_id",
    "target_version",
    "is_foreground",
    "connection_status",
    "connection_response",
    "last_seen_at"
  ];
  fixture.instances.forEach((instance) => {
    required.forEach((field) => assert.ok(field in instance, `${instance.instance_id} missing ${field}`));
    assert.equal(typeof instance.metadata.headless, "boolean");
  });
});

test("recipe and dry-run fixtures preserve review-first production gates", () => {
  assert.match(fixture.recipe.recipe_hash, /^sha256:[0-9a-f]{64}$/);
  assert.equal(fixture.recipe.status, "review_required");
  assert.ok(fixture.recipe.required_gates.includes("cam_simulation"));
  assert.ok(fixture.recipe.required_gates.includes("collision_check"));
  assert.ok(fixture.recipe.required_gates.includes("shop_approval"));

  const response = fixture.command_response;
  assert.equal(response.structured_response.execution_mode, "dry_run");
  assert.equal(response.structured_response.commands_sent, 0);
  const gates = Object.fromEntries(
    response.diff_report.gate_results.map(({ gate, status }) => [gate, status])
  );
  assert.equal(gates.cam_simulation, "not_run");
  assert.equal(gates.collision_check, "required");
  assert.equal(gates.shop_approval, "required");
});

test("learning metrics are wired to API-backed candidate state", () => {
  const webRoot = path.resolve(__dirname, "..");
  const index = fs.readFileSync(path.join(webRoot, "index.html"), "utf8");
  const app = fs.readFileSync(path.join(webRoot, "app.js"), "utf8");

  assert.match(index, /id="candidateCount">0</);
  assert.match(index, /id="candidateSessionCount">0</);
  assert.match(app, /fixture\.candidates\.length/);
  assert.match(app, /candidate\.source_session_ids/);
});
