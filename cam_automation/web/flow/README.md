# CAM Flow Studio isolated component

This directory is a self-contained T07 fixture harness. Open `fixture.html` directly, or
serve this directory as static files. It has no CDN, network, CAM transport, React, or
package-manager dependency.

## Integration boundary

`flow-studio.js` accepts one injected route-neutral adapter:

```js
CAM_FLOW_STUDIO.mount(element, { api });
```

The fixture adapter documents the calls expected by the component:

| Call | Response owned by the adapter |
| --- | --- |
| `listAssets()` | paged `AutomationAsset` items |
| `listCapabilities()` | paged `CapabilityManifest` items |
| `getGraph(graphId)` | canonical `FlowGraph` plus revision ETag |
| `listVersions(graphId)` | immutable `FlowVersion` items |
| `getVersionSnapshot(versionId)` | graph snapshot for deterministic diff |
| `validateGraph(graph)` | stable diagnostics |
| `diffGraph(versionId, graph)` | semantic graph diff with layout excluded |
| `sourceDiff(versionId, graph)` | source candidate evidence |
| `testGraph(graph)` | fixture-only test report |
| `createPreview(request)` | `PreviewPlan` with `transport=none` |

The requested T06 `tests/fixtures/cam-flow/api/**` directory is not present at
`BASE_SHA=53e52e9231f597fe2dec22f4a4990b564dc4d151`, and no T06 dependency SHA was
provided. `FixtureFlowApi` therefore follows the frozen FlowGraph/PreviewPlan contract
and T06 route-neutral capability list. T09 can replace this adapter without changing
the editor or public contracts.

## Renderer replacement boundary

The repository has no React/React Flow dependency, and this task cannot add a package,
CDN, or network fetch. The canvas is a deterministic SVG/DOM projection:

- `GraphStore` owns the only editable canonical `FlowGraph`.
- Node movement, typed connection, configuration, enablement, undo, and redo are
  `GraphCommand` operations.
- SVG paths, DOM focus, pointer state, selection, relation disclosure, and AI display
  state are transient.
- No renderer-specific object is saved into the graph.

A future React Flow view should replace only `renderCanvas`, `renderEdges`, and pointer
event projection. It must consume FlowGraph nodes/edges/layout, emit the existing
GraphCommands, preserve the source/inspector DOM overlays, and never persist React Flow
internal JSON.

## Checks

```powershell
node --test cam_automation/web/flow/flow.test.cjs
```

The harness remains fixture-only. Preview always reports the five frozen zero-execution
fields, and simulation, collision, and shop approval remain independent gates.
