(function attachCamFlowFixtures(global) {
  "use strict";

  const core = global.CAM_FLOW_CORE
    || (typeof require === "function" ? require("./flow-core.js") : null);
  if (!core) {
    throw new Error("CAM_FLOW_CORE must load before flow fixtures.");
  }

  const FIXED_TIME = "2026-08-24T00:00:00Z";

  const sourceLines = [
    "// Fixture-only PowerMill asset; original bytes remain immutable.",
    "FORM BLOCK",
    "EDIT MODEL ALL",
    "EDIT PAR 'Tolerance' 0.050",
    "$VENDOR_PLUGIN OPAQUE_STEP",
    "EDIT TOOLPATH 'ROUGHING'",
    "// end"
  ];
  const assetSource = sourceLines.join("\r\n");
  const assetHash = `sha256:${core.sha256Text(assetSource)}`;
  const lineHash = (line) => `sha256:${core.sha256Text(sourceLines[line - 1] || "")}`;

  function port(portId, direction, edgeKinds, typeRef, cardinality, required, constraints = {}) {
    return {
      port_id: portId,
      direction,
      edge_kinds: edgeKinds,
      type_ref: typeRef,
      cardinality,
      required,
      constraints,
      sensitivity: typeRef === "cam.control" ? "public" : "project_local",
      extensions: {}
    };
  }

  const modules = [
    {
      node_type: "cam.flow.start",
      node_type_version: "1.0.0",
      category: "流程",
      display_name: "流程起点",
      description: "主流程的显式入口。",
      icon: "start",
      ports: [port("next", "output", ["control"], "cam.control", "many", false)],
      configuration_schema: { type: "object", properties: {}, additionalProperties: false },
      default_configuration: {},
      static_risk_floor: "safe",
      required_gates: ["recipe_review"],
      parse_support: true,
      preview_support: true,
      recipe_projection_support: true,
      fidelity: "semantic_round_trip",
      permission: "read:selected-files"
    },
    {
      node_type: "powermill.fixture.stock_context",
      node_type_version: "1.0.0",
      category: "几何上下文",
      display_name: "确认毛坯上下文",
      description: "使用审阅过的 fixture 对象选择器。",
      icon: "stock",
      ports: [
        port("previous", "input", ["control"], "cam.control", "one", true),
        port("next", "output", ["control"], "cam.control", "many", false),
        port("context_ready", "output", ["dependency"], "cam.dependency", "many", false)
      ],
      configuration_schema: {
        type: "object",
        properties: {
          selector: {
            type: "string",
            title: "对象选择器",
            enum: ["fixture:block-reviewed", "fixture:model-all-reviewed"]
          },
          context_status: {
            type: "string",
            title: "上下文状态",
            enum: ["reviewed_fixture", "requires_confirmation"]
          }
        },
        additionalProperties: false
      },
      default_configuration: {
        selector: "fixture:block-reviewed",
        context_status: "reviewed_fixture"
      },
      static_risk_floor: "review",
      required_gates: ["recipe_review", "target_version_validation"],
      parse_support: true,
      preview_support: true,
      recipe_projection_support: true,
      fidelity: "token_exact_outside_edits",
      permission: "read:selected-files"
    },
    {
      node_type: "cam.value.length",
      node_type_version: "1.0.0",
      category: "参数",
      display_name: "长度参数",
      description: "带显式单位的长度值。",
      icon: "parameter",
      ports: [
        port("value", "output", ["data"], "cam.length", "many", false, {
          unit_dimension: "length",
          unit: "mm"
        })
      ],
      configuration_schema: {
        type: "object",
        properties: {
          value: { type: "number", title: "数值", minimum: 0.001, maximum: 1, step: 0.001, unit: "mm" },
          unit: { type: "string", title: "单位", enum: ["mm"] }
        },
        additionalProperties: false
      },
      default_configuration: { value: 0.05, unit: "mm" },
      static_risk_floor: "safe",
      required_gates: ["recipe_review"],
      parse_support: true,
      preview_support: true,
      recipe_projection_support: true,
      fidelity: "semantic_round_trip",
      permission: "read:selected-files"
    },
    {
      node_type: "powermill.fixture.area_clearance",
      node_type_version: "1.0.0",
      category: "工艺步骤",
      display_name: "区域清除参数",
      description: "只形成离线候选和差异。",
      icon: "operation",
      ports: [
        port("previous", "input", ["control"], "cam.control", "one", true),
        port("next", "output", ["control"], "cam.control", "many", false),
        port("context", "input", ["dependency"], "cam.dependency", "one", true),
        port("tolerance", "input", ["data"], "cam.length", "one", true, {
          unit_dimension: "length",
          unit: "mm"
        })
      ],
      configuration_schema: {
        type: "object",
        properties: {
          strategy: {
            type: "string",
            title: "策略",
            enum: ["area_clearance_fixture", "rest_roughing_fixture"]
          },
          boundary_policy: {
            type: "string",
            title: "边界策略",
            enum: ["reviewed_only", "requires_confirmation"]
          },
          enabled_for_fixture: { type: "boolean", title: "纳入 fixture 候选" }
        },
        additionalProperties: false
      },
      default_configuration: {
        strategy: "area_clearance_fixture",
        boundary_policy: "reviewed_only",
        enabled_for_fixture: true
      },
      static_risk_floor: "review",
      required_gates: [
        "recipe_review",
        "target_version_validation",
        "cam_simulation",
        "collision_check",
        "shop_approval"
      ],
      parse_support: true,
      preview_support: true,
      recipe_projection_support: true,
      fidelity: "token_exact_outside_edits",
      permission: "read:selected-files"
    },
    {
      node_type: "cam.fixture.review_point",
      node_type_version: "1.0.0",
      category: "审阅",
      display_name: "人工审阅点",
      description: "保留独立的配方、版本和门禁复核。",
      icon: "review",
      ports: [port("previous", "input", ["control"], "cam.control", "one", true)],
      configuration_schema: {
        type: "object",
        properties: {
          reviewer_scope: {
            type: "string",
            title: "审阅范围",
            enum: ["fixture_only", "source_and_graph"]
          }
        },
        additionalProperties: false
      },
      default_configuration: { reviewer_scope: "source_and_graph" },
      static_risk_floor: "review",
      required_gates: ["recipe_review", "cam_simulation", "collision_check", "shop_approval"],
      parse_support: true,
      preview_support: true,
      recipe_projection_support: true,
      fidelity: "semantic_round_trip",
      permission: "write:local-artifacts"
    },
    {
      node_type: "powermill.fixture.vendor_opaque",
      node_type_version: "1.0.0",
      category: "不透明结构",
      display_name: "商业外挂不透明步骤",
      description: "仅保留原始 span；未获得授权 adapter。",
      icon: "opaque",
      ports: [],
      configuration_schema: { type: "object", properties: {}, additionalProperties: false },
      default_configuration: {},
      static_risk_floor: "blocked",
      required_gates: ["vendor_authorization", "recipe_review"],
      parse_support: false,
      preview_support: false,
      recipe_projection_support: false,
      fidelity: "opaque_preserved",
      permission: "capability:vendor-adapter"
    }
  ];

  const registry = Object.freeze(
    Object.fromEntries(modules.map((module) => [module.node_type, core.clone(module)]))
  );

  const automationAsset = {
    schema_version: 1,
    contract: "cam.automation_asset.v1",
    asset_id: "asset:powermill:fixture:roughing",
    asset_revision_id: "asset-revision:powermill:fixture:roughing:1",
    product: "powermill",
    asset_type: "powermill_macro",
    display_name: "ROUGHING_FIXTURE.mac",
    source_locator: "local-content:asset-revision:powermill:fixture:roughing:1",
    content_hash: assetHash,
    byte_length: assetSource.length,
    encoding: "utf-8",
    bom: "none",
    newline_profile: "crlf",
    immutable: true,
    source_origin: "employer_owned",
    rights: {
      status: "user_asserted",
      evidence_ref: "rights:fixture:roughing:1",
      sharing_scope: "private",
      redistribution_allowed: null,
      network_egress_allowed: false,
      binary_inspection: false
    },
    target_versions: ["PowerMill 2025", "PowerMill 2026"],
    runtime_modes: ["offline"],
    dependencies: [
      {
        dependency_id: "vendor:opaque-step",
        kind: "commercial_addon",
        content_hash: lineHash(5),
        required: false,
        status: "authorization_missing"
      }
    ],
    imported_at: FIXED_TIME,
    extensions: {}
  };

  const capabilityManifest = {
    schema_version: 1,
    contract: "cam.capability_manifest.v1",
    manifest_id: "manifest:powermill:fixture:1",
    manifest_version: "1.0.0",
    manifest_hash: `sha256:${"0".repeat(64)}`,
    provider: {
      provider_id: "cam-automation-studio",
      display_name: "Fixture-only PowerMill adapter",
      claim_level: "fixture_verified"
    },
    product: "powermill",
    adapter_kind: "manifest_only",
    authorization: {
      status: "fixture_verified",
      evidence_ref: "fixture:powermill:flow-studio",
      verified_by: "test-suite",
      valid_until: null
    },
    target_version_ranges: ["PowerMill >=2025,<2027"],
    execution_modes: ["offline", "fixture_dry_run"],
    permissions: ["read:selected-files", "write:local-artifacts"],
    node_types: modules.map((module) => ({
      node_type: module.node_type,
      node_type_version: module.node_type_version,
      category: module.category,
      display_name: module.display_name,
      ports: core.clone(module.ports),
      configuration_schema: core.clone(module.configuration_schema),
      static_risk_floor: module.static_risk_floor,
      required_gates: core.clone(module.required_gates),
      forbidden_modes: ["live"],
      parse_support: module.parse_support,
      preview_support: module.preview_support,
      recipe_projection_support: module.recipe_projection_support,
      fidelity: module.fidelity
    })),
    prohibited_operations: [
      "live_journal",
      "live_macro",
      "nc",
      "gcode",
      "clsf",
      "postprocess",
      "machine_control"
    ],
    evidence: [
      {
        kind: "fixture_test",
        ref: "fixture:powermill:flow-studio",
        version: "1",
        checked_at: FIXED_TIME
      }
    ],
    revocation: null,
    extensions: {
      cam_automation: {
        transport: "none",
        live_connected: false
      }
    }
  };
  const manifestHashProjection = core.clone(capabilityManifest);
  delete manifestHashProjection.manifest_hash;
  capabilityManifest.manifest_hash = core.sha256Canonical(manifestHashProjection);

  function capabilityRef(nodeType) {
    return {
      manifest_id: capabilityManifest.manifest_id,
      manifest_hash: capabilityManifest.manifest_hash,
      node_type: nodeType,
      node_type_version: registry[nodeType].node_type_version
    };
  }

  function createNode(nodeType, nodeId, overrides = {}) {
    const definition = registry[nodeType];
    if (!definition) {
      throw new Error(`Unknown fixture module ${nodeType}.`);
    }
    const opaque = definition.fidelity === "opaque_preserved"
      ? {
          reason: "vendor_adapter_not_authorized",
          asset_revision_id: automationAsset.asset_revision_id,
          source_span_ids: ["mapping:opaque"],
          content_hash: lineHash(5),
          round_trip_policy: "preserve_exact",
          semantic_editable: false,
          diagnostic_codes: ["CAPABILITY_MISSING", "SOURCE_OPAQUE_EDIT"]
        }
      : null;
    return {
      node_id: nodeId,
      node_type: nodeType,
      node_type_version: definition.node_type_version,
      enabled: !opaque,
      risk: definition.static_risk_floor,
      review_status: opaque ? "needs_review" : "accepted",
      port_contract_refs: definition.ports.map(
        (item) => `${nodeType}@${definition.node_type_version}#${item.port_id}`
      ),
      bindings: [],
      configuration: core.clone(definition.default_configuration),
      source_mapping_ids: [],
      fidelity: opaque ? "F3" : definition.fidelity === "semantic_round_trip" ? "F2" : "F1",
      capability_ref: capabilityRef(nodeType),
      compatibility_status: opaque ? "capability_unavailable" : "supported",
      opaque,
      extensions: {},
      ...core.clone(overrides)
    };
  }

  function mapping(mappingId, line, nodeId, role = "primary", quality = "exact", propertyPath = null) {
    const lineStart = sourceLines.slice(0, line - 1).reduce((total, value) => total + value.length + 2, 0);
    const lineValue = sourceLines[line - 1] || "";
    return {
      mapping_id: mappingId,
      asset_id: automationAsset.asset_id,
      asset_revision_id: automationAsset.asset_revision_id,
      source_digest: automationAsset.content_hash,
      source_line: line,
      source_span: {
        start_byte: lineStart,
        end_byte: lineStart + lineValue.length,
        start_line: line,
        end_line: line,
        start_column: 0,
        end_column: lineValue.length,
        column_encoding: "unicode_scalar"
      },
      target: {
        flow_id: "flow:main",
        node_id: nodeId,
        ...(propertyPath ? { property_path: propertyPath } : {})
      },
      role,
      mapping_quality: quality,
      excerpt_hash: lineHash(line),
      extensions: {}
    };
  }

  function edge(edgeId, kind, sourceNode, sourcePort, targetNode, targetPort) {
    return {
      edge_id: edgeId,
      kind,
      source: { node_id: sourceNode, port_id: sourcePort },
      target: { node_id: targetNode, port_id: targetPort },
      condition: null,
      priority: null,
      source_mapping_ids: [],
      extensions: {}
    };
  }

  function makeGraph() {
    const nodes = [
      createNode("cam.flow.start", "node:start", {
        source_mapping_ids: ["mapping:start"]
      }),
      createNode("powermill.fixture.stock_context", "node:stock", {
        source_mapping_ids: ["mapping:stock", "mapping:stock-context"]
      }),
      createNode("cam.value.length", "node:tolerance", {
        source_mapping_ids: ["mapping:tolerance"],
        configuration: { value: 0.05, unit: "mm" }
      }),
      createNode("powermill.fixture.area_clearance", "node:roughing", {
        source_mapping_ids: ["mapping:roughing", "mapping:roughing-tolerance"]
      }),
      createNode("cam.fixture.review_point", "node:review", {
        source_mapping_ids: ["mapping:review"]
      }),
      createNode("powermill.fixture.vendor_opaque", "node:opaque", {
        source_mapping_ids: ["mapping:opaque"]
      })
    ];
    const graph = {
      schema_version: 1,
      contract: "cam.flowgraph.v1",
      graph_id: "graph:powermill:fixture:roughing",
      revision_id: "revision:powermill:fixture:roughing:2",
      parent_revision_id: "revision:powermill:fixture:roughing:1",
      product: "powermill",
      target_versions: ["PowerMill 2025", "PowerMill 2026"],
      entry_flow_id: "flow:main",
      graph_parameters: [
        {
          parameter_id: "parameter:tolerance",
          name: "tolerance",
          type_ref: "cam.length",
          unit: "mm",
          required: true,
          default: 0.05,
          constraints: { minimum: 0.001, maximum: 1 },
          evidence_mapping_ids: ["mapping:tolerance"],
          review_status: "accepted",
          extensions: {}
        }
      ],
      flows: [
        {
          flow_id: "flow:main",
          kind: "main",
          name: "区域清除主流程",
          interface_ports: [],
          parameter_ids: ["parameter:tolerance"],
          nodes,
          edges: [
            edge("edge:start-stock", "control", "node:start", "next", "node:stock", "previous"),
            edge("edge:stock-roughing", "control", "node:stock", "next", "node:roughing", "previous"),
            edge("edge:roughing-review", "control", "node:roughing", "next", "node:review", "previous"),
            edge("edge:stock-context", "dependency", "node:stock", "context_ready", "node:roughing", "context"),
            edge("edge:tolerance-roughing", "data", "node:tolerance", "value", "node:roughing", "tolerance")
          ],
          source_mapping_ids: [
            "mapping:start",
            "mapping:stock",
            "mapping:stock-context",
            "mapping:tolerance",
            "mapping:roughing-tolerance",
            "mapping:roughing",
            "mapping:review",
            "mapping:opaque"
          ],
          extensions: {}
        }
      ],
      asset_refs: [
        {
          asset_id: automationAsset.asset_id,
          asset_revision_id: automationAsset.asset_revision_id,
          content_hash: automationAsset.content_hash,
          encoding: automationAsset.encoding,
          bom: automationAsset.bom,
          newline_profile: automationAsset.newline_profile
        }
      ],
      source_mappings: [
        mapping("mapping:start", 1, "node:start", "control", "synthetic"),
        mapping("mapping:stock", 2, "node:stock"),
        mapping("mapping:stock-context", 3, "node:stock", "parameter", "exact", "/configuration/selector"),
        mapping("mapping:tolerance", 4, "node:tolerance", "parameter", "exact", "/configuration/value"),
        mapping("mapping:roughing-tolerance", 4, "node:roughing", "parameter", "exact", "/configuration/tolerance"),
        mapping("mapping:opaque", 5, "node:opaque", "opaque"),
        mapping("mapping:roughing", 6, "node:roughing"),
        mapping("mapping:review", 7, "node:review", "control", "synthetic")
      ],
      capability_lock: [
        {
          manifest_id: capabilityManifest.manifest_id,
          manifest_version: capabilityManifest.manifest_version,
          manifest_hash: capabilityManifest.manifest_hash
        }
      ],
      required_gates: [
        "recipe_review",
        "target_version_validation",
        "cam_simulation",
        "collision_check",
        "shop_approval"
      ],
      semantic_hash: `sha256:${"0".repeat(64)}`,
      source_snapshot_hash: "sha256:cf0a8c8e4ef8ad4daef4072ebef41565b2e6f02ccc2e6b3df1b911aa6a498468",
      layout: {
        nodes: {
          "node:start": { x: 70, y: 115 },
          "node:stock": { x: 330, y: 115 },
          "node:tolerance": { x: 350, y: 330 },
          "node:roughing": { x: 610, y: 115 },
          "node:review": { x: 900, y: 115 },
          "node:opaque": { x: 900, y: 330 }
        },
        viewport: { x: 0, y: 0, scale: 1 }
      },
      extensions: {
        "cam_automation.fixture": {
          semantic: false,
          transport: "none"
        }
      }
    };
    graph.semantic_hash = core.sha256Canonical(core.semanticSnapshot(graph));
    return graph;
  }

  function makeVersions(graph) {
    const previous = core.clone(graph);
    const lengthNode = core.graphNode(previous, "node:tolerance");
    lengthNode.configuration.value = 0.06;
    previous.graph_parameters[0].default = 0.06;
    previous.parent_revision_id = null;
    previous.revision_id = "revision:powermill:fixture:roughing:1";
    previous.semantic_hash = core.sha256Canonical(core.semanticSnapshot(previous));

    const versionOne = {
      schema_version: 1,
      contract: "cam.flow_version.v1",
      version_id: "flow-version:powermill:fixture:roughing:1",
      graph_id: graph.graph_id,
      revision_id: previous.revision_id,
      parent_version_ids: [],
      semantic_hash: previous.semantic_hash,
      artifact_hash: core.sha256Canonical(previous),
      source_snapshot_hash: previous.source_snapshot_hash,
      capability_lock_hash: core.sha256Canonical(previous.capability_lock),
      status: "review_required",
      author_ref: "user:fixture",
      message: "导入后基线",
      round_trip_report_id: "roundtrip:fixture:roughing:1",
      compatibility_report_id: "compatibility:fixture:roughing:1",
      created_at: "2026-08-24T00:00:01Z",
      immutable: true,
      extensions: {}
    };
    const versionTwo = {
      ...core.clone(versionOne),
      version_id: "flow-version:powermill:fixture:roughing:2",
      revision_id: graph.revision_id,
      parent_version_ids: [versionOne.version_id],
      semantic_hash: graph.semantic_hash,
      artifact_hash: core.sha256Canonical(graph),
      status: "reviewed_for_fixture",
      message: "公差改编候选",
      round_trip_report_id: "roundtrip:fixture:roughing:2",
      compatibility_report_id: "compatibility:fixture:roughing:2",
      created_at: "2026-08-24T00:00:02Z"
    };
    return {
      versions: [versionTwo, versionOne],
      snapshots: {
        [versionOne.version_id]: previous,
        [versionTwo.version_id]: core.clone(graph)
      }
    };
  }

  function makeTargets() {
    return [
      {
        instance_id: "fixture:powermill:4101:C1",
        product: "powermill",
        display_name: "PowerMill fixture · Cavity A",
        target_version: "PowerMill 2026",
        project_id: "fixture-project:pm:cavity-a",
        project_name: "Cavity A test snapshot",
        project_snapshot_hash: "sha256:3333333333333333333333333333333333333333333333333333333333333333",
        is_foreground: false,
        target_kind: "fixture",
        authorized: true,
        transport: "none"
      },
      {
        instance_id: "fixture:powermill:4102:D2",
        product: "powermill",
        display_name: "PowerMill fixture · Electrode B",
        target_version: "PowerMill 2025",
        project_id: "fixture-project:pm:electrode-b",
        project_name: "Electrode B test snapshot",
        project_snapshot_hash: "sha256:4444444444444444444444444444444444444444444444444444444444444444",
        is_foreground: true,
        target_kind: "fixture",
        authorized: true,
        transport: "none"
      },
      {
        instance_id: "fixture:nx:3101:A1",
        product: "nx",
        display_name: "NX fixture · Cavity A",
        target_version: "NX 2406",
        project_id: "fixture-project:nx:cavity-a",
        project_name: "NX Cavity A test snapshot",
        project_snapshot_hash: "sha256:5555555555555555555555555555555555555555555555555555555555555555",
        is_foreground: true,
        target_kind: "fixture",
        authorized: true,
        transport: "none"
      },
      {
        instance_id: "fixture:nx:3102:B2",
        product: "nx",
        display_name: "NX fixture · Electrode B",
        target_version: "NX 2312",
        project_id: "fixture-project:nx:electrode-b",
        project_name: "NX Electrode B test snapshot",
        project_snapshot_hash: "sha256:6666666666666666666666666666666666666666666666666666666666666666",
        is_foreground: false,
        target_kind: "fixture",
        authorized: false,
        transport: "none"
      }
    ];
  }

  function diagnostic(code, severity, message, objectRef, mappingIds, remediation) {
    return {
      code,
      severity,
      message,
      object_ref: objectRef,
      source_mapping_ids: mappingIds,
      remediation
    };
  }

  function makeBundle() {
    const graph = makeGraph();
    const versionData = makeVersions(graph);
    return {
      schema_version: 1,
      contract: "cam.flow.fixture_harness.v1",
      generated_at: FIXED_TIME,
      transport: "none",
      automation_assets: [core.clone(automationAsset)],
      capability_manifests: [core.clone(capabilityManifest)],
      modules: core.clone(modules),
      graph,
      versions: versionData.versions,
      version_snapshots: versionData.snapshots,
      source: {
        asset_revision_id: automationAsset.asset_revision_id,
        display_name: automationAsset.display_name,
        lines: core.clone(sourceLines)
      },
      targets: makeTargets(),
      permissions: {
        "read:selected-files": "granted",
        "write:local-artifacts": "granted",
        "network:declared-hosts": "revoked",
        "capability:vendor-adapter": "missing"
      },
      ai: {
        available: true,
        enabled: false,
        explanation: "AI 仅解释当前选择；确定性 FlowGraph 与校验结果不变。",
        authority: "proposal_only"
      }
    };
  }

  class FixtureFlowApi {
    constructor(bundle = makeBundle()) {
      this.bundle = core.clone(bundle);
      this.previewSequence = 0;
    }

    listAssets() {
      return {
        schema_version: 1,
        items: core.clone(this.bundle.automation_assets),
        next_cursor: null
      };
    }

    listCapabilities() {
      return {
        schema_version: 1,
        items: core.clone(this.bundle.capability_manifests),
        next_cursor: null
      };
    }

    getGraph(graphId) {
      if (graphId && graphId !== this.bundle.graph.graph_id) {
        throw new core.FlowError("FLOW_NOT_FOUND", `Graph ${graphId} does not exist.`);
      }
      return { schema_version: 1, graph: core.clone(this.bundle.graph), etag: this.bundle.graph.revision_id };
    }

    listVersions(graphId) {
      if (graphId !== this.bundle.graph.graph_id) {
        throw new core.FlowError("FLOW_NOT_FOUND", `Graph ${graphId} does not exist.`);
      }
      return { schema_version: 1, items: core.clone(this.bundle.versions), next_cursor: null };
    }

    getVersionSnapshot(versionId) {
      const snapshot = this.bundle.version_snapshots[versionId];
      if (!snapshot) {
        throw new core.FlowError("FLOW_VERSION_NOT_FOUND", `Version ${versionId} does not exist.`);
      }
      return core.clone(snapshot);
    }

    validateGraph(graph) {
      const diagnostics = [];
      const flow = core.graphFlow(graph);
      flow.edges.forEach((item) => {
        const issue = core.connectionIssue(graph, registry, item);
        if (issue) {
          diagnostics.push(
            diagnostic(issue.code, "blocker", issue.message, item.edge_id, item.source_mapping_ids || [], "检查端口类型与关系。")
          );
        }
      });
      flow.nodes.forEach((node) => {
        const definition = registry[node.node_type];
        if (!definition) {
          diagnostics.push(
            diagnostic("CAPABILITY_MISSING", "blocker", "节点能力未注册。", node.node_id, node.source_mapping_ids, "安装并核验 capability manifest。")
          );
          return;
        }
        if (node.opaque) {
          diagnostics.push(
            diagnostic(
              "SOURCE_OPAQUE_EDIT",
              node.enabled ? "blocker" : "warning",
              "不透明结构仅保留原始 source span，语义编辑已关闭。",
              node.node_id,
              node.source_mapping_ids,
              "保持禁用，或取得有权使用的签名能力清单。"
            )
          );
        }
        if (this.bundle.permissions[definition.permission] !== "granted") {
          diagnostics.push(
            diagnostic(
              "CAPABILITY_MISSING",
              node.enabled ? "blocker" : "warning",
              `所需权限 ${definition.permission} 不可用。`,
              node.node_id,
              node.source_mapping_ids,
              "核对授权范围；不会自动恢复任务。"
            )
          );
        }
      });
      const order = { blocker: 0, error: 1, warning: 2, info: 3 };
      diagnostics.sort(
        (left, right) =>
          order[left.severity] - order[right.severity]
          || left.code.localeCompare(right.code)
          || left.object_ref.localeCompare(right.object_ref)
      );
      return {
        schema_version: 1,
        graph_id: graph.graph_id,
        revision_id: graph.revision_id,
        status: diagnostics.some((item) => item.severity === "blocker") ? "blocked" : "passed",
        diagnostics
      };
    }

    diffGraph(baseVersionId, graph) {
      return core.diffGraphs(this.getVersionSnapshot(baseVersionId), graph);
    }

    sourceDiff(baseVersionId, graph) {
      const base = this.getVersionSnapshot(baseVersionId);
      const before = core.graphNode(base, "node:tolerance").configuration.value;
      const after = core.graphNode(graph, "node:tolerance").configuration.value;
      const changed = before !== after;
      return {
        schema_version: 1,
        contract: "cam.source_diff.fixture.v1",
        asset_revision_id: automationAsset.asset_revision_id,
        untouched_spans_exact: true,
        opaque_spans_preserved: true,
        candidate_reparsed: true,
        changes: changed
          ? [
              {
                mapping_id: "mapping:tolerance",
                line: 4,
                before: `EDIT PAR 'Tolerance' ${Number(before).toFixed(3)}`,
                after: `EDIT PAR 'Tolerance' ${Number(after).toFixed(3)}`
              }
            ]
          : []
      };
    }

    testGraph(graph) {
      const validation = this.validateGraph(graph);
      const checks = [
        { name: "Schema 与 typed ports", status: validation.status === "passed" ? "passed" : "blocked" },
        { name: "source mapping 双向索引", status: graph.source_mappings.length > 0 ? "passed" : "blocked" },
        { name: "opaque span 保留", status: core.graphNode(graph, "node:opaque") ? "passed" : "blocked" },
        { name: "candidate reparse fixture", status: "passed" },
        { name: "network egress", status: "none" }
      ];
      return {
        schema_version: 1,
        contract: "cam.flow_test_report.fixture.v1",
        graph_id: graph.graph_id,
        revision_id: graph.revision_id,
        status: checks.some((item) => item.status === "blocked") ? "blocked" : "passed",
        checks,
        diagnostics: validation.diagnostics,
        transport: "none"
      };
    }

    createPreview({ graph, flow_version_id: flowVersionId, target }) {
      const required = ["product", "target_version", "target_instance_id", "project_id", "project_snapshot_hash"];
      const missing = required.filter((field) => !target || !target[field]);
      if (missing.length) {
        throw new core.FlowError("PREVIEW_TARGET_AMBIGUOUS", `缺少目标字段：${missing.join(", ")}`);
      }
      const fixtureTarget = this.bundle.targets.find(
        (item) =>
          item.instance_id === target.target_instance_id
          && item.project_id === target.project_id
          && item.target_version === target.target_version
      );
      if (!fixtureTarget || target.product !== graph.product || fixtureTarget.product !== graph.product) {
        throw new core.FlowError("PREVIEW_TARGET_AMBIGUOUS", "目标产品、版本、实例与项目必须精确匹配。");
      }
      if (!fixtureTarget.authorized) {
        throw new core.FlowError("PREVIEW_PERMISSION_REVOKED", "fixture 目标授权不可用。");
      }
      const validation = this.validateGraph(graph);
      if (validation.status === "blocked") {
        throw new core.FlowError("FLOW_SCHEMA_INVALID", "当前图存在 blocker，不能形成预览。", {
          diagnostics: validation.diagnostics
        });
      }
      this.previewSequence += 1;
      return {
        schema_version: 1,
        contract: "cam.preview_plan.v1",
        plan_id: `preview:fixture:ui:${String(this.previewSequence).padStart(3, "0")}`,
        graph_id: graph.graph_id,
        revision_id: graph.revision_id,
        flow_version_id: flowVersionId,
        status: "succeeded",
        execution_mode: "fixture_dry_run",
        transport: "none",
        target: {
          product: target.product,
          target_version: target.target_version,
          target_instance_id: target.target_instance_id,
          project_id: target.project_id,
          project_snapshot_hash: target.project_snapshot_hash,
          target_kind: "fixture"
        },
        hashes: {
          source_snapshot_hash: graph.source_snapshot_hash,
          semantic_hash: graph.semantic_hash,
          capability_lock_hash: core.sha256Canonical(graph.capability_lock),
          reviewed_recipe_hash: null
        },
        steps: core.graphFlow(graph).nodes
          .filter((node) => node.enabled)
          .map((node, index) => ({
            order: index + 1,
            node_id: node.node_id,
            action: node.node_type,
            parameters: core.clone(node.configuration),
            source_mapping_ids: core.clone(node.source_mapping_ids),
            effects: ["fixture_candidate_only"],
            risk: node.risk,
            compatibility_status: node.compatibility_status,
            approval_required: node.risk !== "safe"
          })),
        gate_results: [
          { gate: "recipe_review", status: "passed" },
          { gate: "target_version_validation", status: "passed" },
          { gate: "cam_simulation", status: "not_run" },
          { gate: "collision_check", status: "required" },
          { gate: "shop_approval", status: "required" }
        ],
        round_trip_report_id: "roundtrip:fixture:roughing:2",
        compatibility_report_id: "compatibility:fixture:roughing:2",
        diagnostics: [],
        commands_sent: 0,
        journal_executed: false,
        macro_executed: false,
        machine_output_count: 0,
        created_at: FIXED_TIME,
        completed_at: FIXED_TIME,
        extensions: {}
      };
    }
  }

  function makePerformanceGraph(count = 500) {
    const graph = makeGraph();
    const flow = core.graphFlow(graph);
    flow.nodes = [];
    flow.edges = [];
    graph.layout.nodes = {};
    for (let index = 0; index < count; index += 1) {
      const nodeId = `node:perf:${String(index).padStart(3, "0")}`;
      flow.nodes.push(createNode("cam.flow.start", nodeId, { source_mapping_ids: [] }));
      graph.layout.nodes[nodeId] = {
        x: (index % 25) * 238,
        y: Math.floor(index / 25) * 152
      };
    }
    graph.semantic_hash = core.sha256Canonical(core.semanticSnapshot(graph));
    return graph;
  }

  const api = Object.freeze({
    FixtureFlowApi,
    createNode,
    makeBundle,
    makePerformanceGraph,
    registry
  });

  global.CAM_FLOW_FIXTURES = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(globalThis);
