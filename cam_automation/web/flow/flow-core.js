(function attachCamFlowCore(global) {
  "use strict";

  const HASH_PATTERN = /^sha256:[0-9a-f]{64}$/;
  const SEMANTIC_EDGE_FIELDS = [
    "edge_id",
    "kind",
    "source",
    "target",
    "condition",
    "priority",
    "extensions"
  ];

  function clone(value) {
    if (typeof structuredClone === "function") {
      return structuredClone(value);
    }
    return JSON.parse(JSON.stringify(value));
  }

  function canonicalize(value) {
    if (Array.isArray(value)) {
      return value.map(canonicalize);
    }
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.keys(value)
          .sort()
          .map((key) => [key, canonicalize(value[key])])
      );
    }
    return value;
  }

  function canonicalJson(value, spacing = 0) {
    return JSON.stringify(canonicalize(value), null, spacing);
  }

  function utf8Bytes(value) {
    if (typeof TextEncoder !== "undefined") {
      return new TextEncoder().encode(value);
    }
    return Uint8Array.from(Buffer.from(value, "utf8"));
  }

  function rotateRight(value, amount) {
    return (value >>> amount) | (value << (32 - amount));
  }

  // Small synchronous SHA-256 keeps graph hashes deterministic in file:// fixtures.
  function sha256Text(value) {
    const bytes = Array.from(utf8Bytes(value));
    const bitLength = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) {
      bytes.push(0);
    }
    const high = Math.floor(bitLength / 0x100000000);
    const low = bitLength >>> 0;
    for (let shift = 24; shift >= 0; shift -= 8) {
      bytes.push((high >>> shift) & 0xff);
    }
    for (let shift = 24; shift >= 0; shift -= 8) {
      bytes.push((low >>> shift) & 0xff);
    }

    const constants = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
      0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
      0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
      0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
      0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ];
    const hash = [
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
    ];
    const words = new Uint32Array(64);

    for (let offset = 0; offset < bytes.length; offset += 64) {
      for (let index = 0; index < 16; index += 1) {
        const start = offset + index * 4;
        words[index] = (
          (bytes[start] << 24)
          | (bytes[start + 1] << 16)
          | (bytes[start + 2] << 8)
          | bytes[start + 3]
        ) >>> 0;
      }
      for (let index = 16; index < 64; index += 1) {
        const previous = words[index - 15];
        const farPrevious = words[index - 2];
        const sigma0 = rotateRight(previous, 7) ^ rotateRight(previous, 18) ^ (previous >>> 3);
        const sigma1 = rotateRight(farPrevious, 17) ^ rotateRight(farPrevious, 19) ^ (farPrevious >>> 10);
        words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0;
      }

      let [a, b, c, d, e, f, g, h] = hash;
      for (let index = 0; index < 64; index += 1) {
        const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
        const choice = (e & f) ^ (~e & g);
        const temp1 = (h + sum1 + choice + constants[index] + words[index]) >>> 0;
        const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (sum0 + majority) >>> 0;
        h = g;
        g = f;
        f = e;
        e = (d + temp1) >>> 0;
        d = c;
        c = b;
        b = a;
        a = (temp1 + temp2) >>> 0;
      }
      hash[0] = (hash[0] + a) >>> 0;
      hash[1] = (hash[1] + b) >>> 0;
      hash[2] = (hash[2] + c) >>> 0;
      hash[3] = (hash[3] + d) >>> 0;
      hash[4] = (hash[4] + e) >>> 0;
      hash[5] = (hash[5] + f) >>> 0;
      hash[6] = (hash[6] + g) >>> 0;
      hash[7] = (hash[7] + h) >>> 0;
    }

    return hash.map((word) => word.toString(16).padStart(8, "0")).join("");
  }

  function sha256Canonical(value) {
    return `sha256:${sha256Text(canonicalJson(value))}`;
  }

  function pick(source, fields) {
    return Object.fromEntries(fields.filter((field) => field in source).map((field) => [field, source[field]]));
  }

  function semanticExtensions(extensions) {
    return Object.fromEntries(
      Object.entries(extensions || {})
        .filter(([, value]) => value && typeof value === "object" && value.semantic === true)
        .sort(([left], [right]) => left.localeCompare(right))
    );
  }

  function semanticBinding(binding) {
    return {
      binding_id: binding.binding_id,
      target: clone(binding.target),
      kind: binding.kind,
      literal: clone(binding.literal),
      graph_parameter_id: binding.graph_parameter_id,
      source_output: clone(binding.source_output),
      secret_ref: binding.secret_ref,
      extensions: semanticExtensions(binding.extensions)
    };
  }

  function semanticNode(node) {
    const result = {
      node_id: node.node_id,
      node_type: node.node_type,
      node_type_version: node.node_type_version,
      enabled: node.enabled,
      risk: node.risk,
      bindings: (node.bindings || [])
        .map(semanticBinding)
        .sort((left, right) => String(left.binding_id).localeCompare(String(right.binding_id))),
      configuration: clone(node.configuration || {}),
      extensions: semanticExtensions(node.extensions)
    };
    if (node.opaque && typeof node.opaque === "object") {
      result.opaque = pick(node.opaque, [
        "reason",
        "asset_revision_id",
        "content_hash",
        "round_trip_policy",
        "semantic_editable"
      ]);
    }
    return result;
  }

  function semanticEdge(edge) {
    return {
      ...pick(edge, SEMANTIC_EDGE_FIELDS.filter((field) => field !== "extensions")),
      extensions: semanticExtensions(edge.extensions)
    };
  }

  function semanticParameter(parameter) {
    const value = Object.fromEntries(
      Object.entries(parameter)
        .filter(([key]) => !["evidence_mapping_ids", "review_status", "source_mapping_ids", "extensions"].includes(key))
    );
    return { ...value, extensions: semanticExtensions(parameter.extensions) };
  }

  function semanticSnapshot(graph) {
    return {
      schema_version: graph.schema_version,
      contract: graph.contract,
      product: graph.product,
      target_versions: Array.from(new Set(graph.target_versions || [])).sort(),
      graph_parameters: (graph.graph_parameters || [])
        .map(semanticParameter)
        .sort((left, right) => String(left.parameter_id).localeCompare(String(right.parameter_id))),
      entry_flow_id: graph.entry_flow_id,
      flows: (graph.flows || [])
        .map((flow) => ({
          flow_id: flow.flow_id,
          kind: flow.kind,
          interface_ports: clone(flow.interface_ports || [])
            .sort((left, right) => String(left.port_id).localeCompare(String(right.port_id))),
          parameter_ids: Array.from(new Set(flow.parameter_ids || [])).sort(),
          nodes: (flow.nodes || [])
            .map(semanticNode)
            .sort((left, right) => String(left.node_id).localeCompare(String(right.node_id))),
          edges: (flow.edges || [])
            .map(semanticEdge)
            .sort((left, right) => String(left.edge_id).localeCompare(String(right.edge_id))),
          extensions: semanticExtensions(flow.extensions)
        }))
        .sort((left, right) => String(left.flow_id).localeCompare(String(right.flow_id))),
      capability_lock: clone(graph.capability_lock || [])
        .sort((left, right) =>
          String(left.manifest_id).localeCompare(String(right.manifest_id))
          || String(left.manifest_version).localeCompare(String(right.manifest_version))
          || String(left.manifest_hash).localeCompare(String(right.manifest_hash))
        ),
      required_gates: Array.from(new Set(graph.required_gates || [])).sort(),
      extensions: semanticExtensions(graph.extensions)
    };
  }

  function rehashGraph(graph, previousRevisionId) {
    const next = clone(graph);
    const digest = sha256Canonical(semanticSnapshot(next));
    next.parent_revision_id = previousRevisionId || next.revision_id || null;
    next.semantic_hash = digest;
    next.revision_id = `revision:ui:${digest.slice(7, 23)}`;
    return next;
  }

  function graphFlow(graph, flowId) {
    const id = flowId || graph.entry_flow_id;
    const flow = (graph.flows || []).find((item) => item.flow_id === id);
    if (!flow) {
      throw new FlowError("FLOW_ENTRY_INVALID", `Flow ${id} does not exist.`);
    }
    return flow;
  }

  function graphNode(graph, nodeId, flowId) {
    return graphFlow(graph, flowId).nodes.find((node) => node.node_id === nodeId) || null;
  }

  function registryEntry(registry, nodeType) {
    if (registry instanceof Map) {
      return registry.get(nodeType);
    }
    return registry[nodeType];
  }

  function portFor(registry, graph, endpoint) {
    const node = graphNode(graph, endpoint.node_id);
    const definition = node && registryEntry(registry, node.node_type);
    const port = definition && (definition.ports || []).find((item) => item.port_id === endpoint.port_id);
    return { node, definition, port };
  }

  class FlowError extends Error {
    constructor(code, message, details = {}) {
      super(message);
      this.name = "FlowError";
      this.code = code;
      this.details = details;
    }
  }

  function connectionIssue(graph, registry, connection) {
    const source = portFor(registry, graph, connection.source);
    const target = portFor(registry, graph, connection.target);
    const kind = connection.kind;
    if (!source.node || !source.port || !target.node || !target.port) {
      return new FlowError("PORT_NOT_FOUND", "连接端口不存在。", connection);
    }
    if (source.port.direction !== "output" || target.port.direction !== "input") {
      return new FlowError("PORT_DIRECTION_INVALID", "连接必须从输出端口指向输入端口。", connection);
    }
    if (!(source.port.edge_kinds || []).includes(kind) || !(target.port.edge_kinds || []).includes(kind)) {
      return new FlowError("PORT_KIND_MISMATCH", `端口不接受 ${kind} 关系。`, connection);
    }
    if (source.port.type_ref !== target.port.type_ref) {
      return new FlowError(
        "PORT_TYPE_MISMATCH",
        `${source.port.type_ref} 不能连接到 ${target.port.type_ref}。`,
        connection
      );
    }
    const sourceUnit = source.port.constraints && source.port.constraints.unit;
    const targetUnit = target.port.constraints && target.port.constraints.unit;
    if (sourceUnit && targetUnit && sourceUnit !== targetUnit) {
      return new FlowError("PORT_UNIT_MISMATCH", `${sourceUnit} 与 ${targetUnit} 需要显式转换节点。`, connection);
    }
    if ((source.node.opaque || target.node.opaque) && kind === "data") {
      return new FlowError("SOURCE_OPAQUE_EDIT", "不透明数据不能连接已知数据端口。", connection);
    }
    if (["one", "optional"].includes(target.port.cardinality)) {
      const incoming = graphFlow(graph).edges.filter(
        (edge) =>
          edge.edge_id !== connection.edge_id
          &&
          edge.kind === kind
          && edge.target.node_id === connection.target.node_id
          && edge.target.port_id === connection.target.port_id
      );
      if (incoming.length > 0) {
        return new FlowError("PORT_CARDINALITY_EXCEEDED", "目标端口只允许一个来源。", connection);
      }
    }
    return null;
  }

  function assertConnection(graph, registry, connection) {
    const issue = connectionIssue(graph, registry, connection);
    if (issue) {
      throw issue;
    }
    return true;
  }

  class GraphCommand {
    constructor({ commandId, type, label, semantic = true, transform }) {
      if (typeof transform !== "function") {
        throw new TypeError("GraphCommand requires a transform function.");
      }
      this.command_id = commandId;
      this.type = type;
      this.label = label;
      this.semantic = semantic;
      this.transform = transform;
      this.before = null;
      this.after = null;
    }

    apply(graph) {
      if (!this.after) {
        this.before = clone(graph);
        const transformed = this.transform(clone(graph));
        this.after = this.semantic
          ? rehashGraph(transformed, this.before.revision_id)
          : clone(transformed);
      }
      return clone(this.after);
    }

    revert() {
      if (!this.before) {
        throw new Error("Cannot undo a GraphCommand before it is applied.");
      }
      return clone(this.before);
    }
  }

  class GraphStore {
    constructor(graph) {
      this.graph = clone(graph);
      this.undoStack = [];
      this.redoStack = [];
      this.listeners = new Set();
    }

    snapshot() {
      return clone(this.graph);
    }

    canonicalJson(spacing = 0) {
      return canonicalJson(this.graph, spacing);
    }

    execute(command) {
      if (!(command instanceof GraphCommand)) {
        throw new TypeError("GraphStore.execute expects a GraphCommand.");
      }
      this.graph = command.apply(this.graph);
      this.undoStack.push(command);
      this.redoStack = [];
      this.emit({ type: "execute", command });
      return this.snapshot();
    }

    undo() {
      const command = this.undoStack.pop();
      if (!command) {
        return this.snapshot();
      }
      this.graph = command.revert();
      this.redoStack.push(command);
      this.emit({ type: "undo", command });
      return this.snapshot();
    }

    redo() {
      const command = this.redoStack.pop();
      if (!command) {
        return this.snapshot();
      }
      this.graph = command.apply(this.graph);
      this.undoStack.push(command);
      this.emit({ type: "redo", command });
      return this.snapshot();
    }

    replace(graph, reason = "replace") {
      this.graph = clone(graph);
      this.undoStack = [];
      this.redoStack = [];
      this.emit({ type: reason, command: null });
    }

    subscribe(listener) {
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    emit(event) {
      this.listeners.forEach((listener) => listener(this.snapshot(), event));
    }

    get canUndo() {
      return this.undoStack.length > 0;
    }

    get canRedo() {
      return this.redoStack.length > 0;
    }
  }

  function commandId(type, value) {
    return `graph-command:${type}:${sha256Text(String(value)).slice(0, 12)}`;
  }

  function moveNodeCommand(nodeId, position) {
    return new GraphCommand({
      commandId: commandId("move-node", `${nodeId}:${position.x}:${position.y}`),
      type: "move_node",
      label: `移动 ${nodeId}`,
      semantic: false,
      transform(graph) {
        if (!graphNode(graph, nodeId)) {
          throw new FlowError("FLOW_NODE_NOT_FOUND", `Node ${nodeId} does not exist.`);
        }
        graph.layout = graph.layout || {};
        graph.layout.nodes = graph.layout.nodes || {};
        graph.layout.nodes[nodeId] = {
          x: Math.round(position.x),
          y: Math.round(position.y)
        };
        return graph;
      }
    });
  }

  function updateConfigurationCommand(nodeId, property, value) {
    return new GraphCommand({
      commandId: commandId("configure-node", `${nodeId}:${property}:${canonicalJson(value)}`),
      type: "update_configuration",
      label: `修改 ${nodeId} · ${property}`,
      transform(graph) {
        const node = graphNode(graph, nodeId);
        if (!node) {
          throw new FlowError("FLOW_NODE_NOT_FOUND", `Node ${nodeId} does not exist.`);
        }
        if (node.opaque && node.opaque.semantic_editable === false) {
          throw new FlowError("SOURCE_OPAQUE_EDIT", "不透明节点为只读。", { node_id: nodeId });
        }
        node.configuration = node.configuration || {};
        node.configuration[property] = clone(value);
        return graph;
      }
    });
  }

  function toggleNodeCommand(nodeId, enabled) {
    return new GraphCommand({
      commandId: commandId("toggle-node", `${nodeId}:${enabled}`),
      type: "toggle_node",
      label: `${enabled ? "启用" : "停用"} ${nodeId}`,
      transform(graph) {
        const node = graphNode(graph, nodeId);
        if (!node) {
          throw new FlowError("FLOW_NODE_NOT_FOUND", `Node ${nodeId} does not exist.`);
        }
        if (node.opaque && node.opaque.semantic_editable === false) {
          throw new FlowError("SOURCE_OPAQUE_EDIT", "不透明节点为只读。", { node_id: nodeId });
        }
        node.enabled = Boolean(enabled);
        node.review_status = "needs_review";
        return graph;
      }
    });
  }

  function addNodeCommand(node, position, flowId) {
    return new GraphCommand({
      commandId: commandId("add-node", `${node.node_id}:${position.x}:${position.y}`),
      type: "add_node",
      label: `添加 ${node.node_id}`,
      transform(graph) {
        const flow = graphFlow(graph, flowId);
        if (flow.nodes.some((item) => item.node_id === node.node_id)) {
          throw new FlowError("FLOW_NODE_DUPLICATE", `Node ${node.node_id} already exists.`);
        }
        flow.nodes.push(clone(node));
        graph.layout = graph.layout || {};
        graph.layout.nodes = graph.layout.nodes || {};
        graph.layout.nodes[node.node_id] = {
          x: Math.round(position.x),
          y: Math.round(position.y)
        };
        return graph;
      }
    });
  }

  function connectCommand(connection, registry) {
    const edgeId = connection.edge_id || [
      "edge",
      connection.kind,
      connection.source.node_id,
      connection.source.port_id,
      connection.target.node_id,
      connection.target.port_id
    ].join(":");
    return new GraphCommand({
      commandId: commandId("connect", edgeId),
      type: "connect",
      label: `连接 ${connection.source.node_id} → ${connection.target.node_id}`,
      transform(graph) {
        assertConnection(graph, registry, connection);
        const flow = graphFlow(graph);
        if (flow.edges.some((edge) => edge.edge_id === edgeId)) {
          throw new FlowError("FLOW_EDGE_DUPLICATE", `Edge ${edgeId} already exists.`);
        }
        flow.edges.push({
          edge_id: edgeId,
          kind: connection.kind,
          source: clone(connection.source),
          target: clone(connection.target),
          condition: null,
          priority: null,
          source_mapping_ids: [],
          extensions: {}
        });
        return graph;
      }
    });
  }

  function sourceMappingsForNode(graph, nodeId) {
    const node = graphNode(graph, nodeId);
    if (!node) {
      return [];
    }
    const ids = new Set(node.source_mapping_ids || []);
    return (graph.source_mappings || [])
      .filter((mapping) => ids.has(mapping.mapping_id) || mapping.target.node_id === nodeId)
      .sort((left, right) => {
        const leftSize = left.source_span.end_byte - left.source_span.start_byte;
        const rightSize = right.source_span.end_byte - right.source_span.start_byte;
        const quality = { exact: 0, derived: 1, ambiguous: 2, synthetic: 3 };
        return leftSize - rightSize
          || (quality[left.mapping_quality] ?? 9) - (quality[right.mapping_quality] ?? 9)
          || left.mapping_id.localeCompare(right.mapping_id);
      })
      .map(clone);
  }

  function nodesForSourceLine(graph, assetRevisionId, line) {
    const mappings = (graph.source_mappings || [])
      .filter(
        (mapping) =>
          mapping.asset_revision_id === assetRevisionId
          && mapping.source_span.start_line <= line
          && mapping.source_span.end_line >= line
      )
      .sort((left, right) => {
        const leftSize = left.source_span.end_byte - left.source_span.start_byte;
        const rightSize = right.source_span.end_byte - right.source_span.start_byte;
        const quality = { exact: 0, derived: 1, ambiguous: 2, synthetic: 3 };
        return leftSize - rightSize
          || (quality[left.mapping_quality] ?? 9) - (quality[right.mapping_quality] ?? 9)
          || left.mapping_id.localeCompare(right.mapping_id);
      });
    return mappings.map((mapping) => ({
      node_id: mapping.target.node_id,
      mapping_id: mapping.mapping_id,
      mapping_quality: mapping.mapping_quality,
      property_path: mapping.target.property_path || null
    }));
  }

  function diffGraphs(base, candidate) {
    const changes = [];
    const baseFlow = graphFlow(base);
    const candidateFlow = graphFlow(candidate);
    const baseNodes = new Map(baseFlow.nodes.map((node) => [node.node_id, node]));
    const candidateNodes = new Map(candidateFlow.nodes.map((node) => [node.node_id, node]));
    const ids = Array.from(new Set([...baseNodes.keys(), ...candidateNodes.keys()])).sort();
    ids.forEach((nodeId) => {
      const before = baseNodes.get(nodeId);
      const after = candidateNodes.get(nodeId);
      if (!before) {
        changes.push({ kind: "node_added", object_ref: nodeId, before: null, after: after.node_type });
      } else if (!after) {
        changes.push({ kind: "node_removed", object_ref: nodeId, before: before.node_type, after: null });
      } else if (canonicalJson(semanticNode(before)) !== canonicalJson(semanticNode(after))) {
        changes.push({
          kind: "node_changed",
          object_ref: nodeId,
          before: pick(before, ["enabled", "configuration", "review_status"]),
          after: pick(after, ["enabled", "configuration", "review_status"])
        });
      }
    });
    const baseEdges = new Map(baseFlow.edges.map((edge) => [edge.edge_id, edge]));
    const candidateEdges = new Map(candidateFlow.edges.map((edge) => [edge.edge_id, edge]));
    Array.from(new Set([...baseEdges.keys(), ...candidateEdges.keys()])).sort().forEach((edgeId) => {
      if (!baseEdges.has(edgeId)) {
        changes.push({ kind: "edge_added", object_ref: edgeId });
      } else if (!candidateEdges.has(edgeId)) {
        changes.push({ kind: "edge_removed", object_ref: edgeId });
      } else if (canonicalJson(semanticEdge(baseEdges.get(edgeId))) !== canonicalJson(semanticEdge(candidateEdges.get(edgeId)))) {
        changes.push({ kind: "edge_changed", object_ref: edgeId });
      }
    });
    return {
      schema_version: 1,
      contract: "cam.graph_diff.fixture.v1",
      base_revision_id: base.revision_id,
      candidate_revision_id: candidate.revision_id,
      semantic_changed: changes.length > 0,
      layout_excluded: true,
      changes
    };
  }

  function visibleNodes(graph, viewport, dimensions = { width: 188, height: 116 }) {
    const started = typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
    const scale = viewport.scale || 1;
    const left = (-viewport.x) / scale;
    const top = (-viewport.y) / scale;
    const right = left + viewport.width / scale;
    const bottom = top + viewport.height / scale;
    const margin = 120 / scale;
    const positions = (graph.layout && graph.layout.nodes) || {};
    const nodes = graphFlow(graph).nodes.filter((node) => {
      const point = positions[node.node_id] || { x: 0, y: 0 };
      return point.x + dimensions.width >= left - margin
        && point.x <= right + margin
        && point.y + dimensions.height >= top - margin
        && point.y <= bottom + margin;
    });
    const ended = typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
    return {
      nodes,
      total_nodes: graphFlow(graph).nodes.length,
      visible_nodes: nodes.length,
      duration_ms: Number((ended - started).toFixed(3))
    };
  }

  const api = Object.freeze({
    HASH_PATTERN,
    FlowError,
    GraphCommand,
    GraphStore,
    addNodeCommand,
    assertConnection,
    canonicalJson,
    clone,
    connectCommand,
    connectionIssue,
    diffGraphs,
    graphFlow,
    graphNode,
    moveNodeCommand,
    nodesForSourceLine,
    rehashGraph,
    semanticSnapshot,
    sha256Text,
    sha256Canonical,
    sourceMappingsForNode,
    toggleNodeCommand,
    updateConfigurationCommand,
    visibleNodes
  });

  global.CAM_FLOW_CORE = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(globalThis);
