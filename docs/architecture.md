# Architecture

## CAM Automation Studio 0.5

The application starts as a minimal core. Product and operational modules are discovered from
`plugins/*/app-plugin.json`, but none are installed by default:

```text
static UI + health
        |
        v
 PluginManager ----> app-plugin.json catalog
        |                     |
        | install             +--> features + dependencies + permissions
        v
 installed-plugins.json
        |
        +--> RecorderService + EventQuery/EventPage
        +--> ConnectionMonitor + NX/PowerMill transports
        +--> SessionService + WorkflowLearner + RecipeService
        +--> CommandTaskService + DiagnosticsService
        +--> Codex Bridge v1
```

`cam_automation/plugin_manager.py` owns discovery, dependency validation, the empty-by-default
installation registry, atomic persistence, and uninstall dependency checks. The execution gateway
depends on local capture so every accepted or rejected execution request has an audit path.
Removing a plugin changes the registry and unloads long-running services; it does not delete user
data.

`cam_automation/integrations.py` is the product-facing service boundary. It keeps the NX parser in
`plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py` and the PowerMill vocabulary in
`cam_automation/profiles/powermill.py`. The web server and UI consume the same response shape for
both products. Product-only actions remain prefixed with `nx.*` or `powermill.*`; shared operations
use `cam.*`.

`cam_automation/codex_bridge.py` implements `cam.codex.bridge.v1`. It exports evidence and review
instructions, validates strict `CodexReviewRequest` and `CodexReviewResult` objects, rejects prompt
injection and machine-output content, and can persist an exchange bundle inside an explicit local
boundary. It does not execute returned text or attach to a CAD/CAM process.

`cam_automation/recorder.py` owns background source discovery, process detection, redaction,
deduplication, SQLite persistence, and JSONL export. It reads only configured CAM source types and
is not imported until `cam-local-capture` is installed. On first installation the local capture
plugin grants a reversible local-only consent and starts collection automatically. Users can revoke
the grant or disable log capture, instance detection, and execution audit independently in advanced
settings. Revocation stops collection and clears in-memory window metadata without deleting saved
local data; a previously revoked configuration is not auto-authorized again after reinstall.
`cam_automation/connection_monitor.py` enumerates CAM host processes and visible top-level windows
without attaching to them. Runtime instance IDs combine product, PID, and window handle so the UI
can distinguish multiple NX/PowerMill windows. The plugin home always shows compact Codex, UG/NX,
and PowerMill connection summaries; the workbench connection panel expands instance details.
Window titles remain ephemeral local status and are not persisted into learned events.
`cam_automation/command_tasks.py` owns immutable task snapshots, exact five-part target binding,
per-instance serial queues, cancellation, timeouts, bounded result retention, response attribution,
and response rescanning. `cam_automation/diagnostics.py` reports the same queues and connection
heartbeats used by the API. `cam_automation/execution.py` remains the fail-closed compatibility
gateway. Dry-run is the only built-in mutation mode, and attempts are written back as ActivityEvent
`execution_audit` evidence when recording consent exists.

`cam_automation/fixture_runtime.py` assembles the default repository demo graph. It discovers two
NX and two PowerMill fixture instances, authorizes only `nx:3101:A1` and
`powermill:4101:C1`, and registers offline transports with `live_connected=false`. This graph is
for deterministic integration and browser acceptance only; it does not claim a live CAM session.

## End-to-End Data Flow

```text
NX Journal / PowerMill macro or log / ActivityEvent JSONL
                         |
                         v
                product-isolated parser
                         |
                         v
        RecorderService -> EventPage -> SessionService
                                         |
                                         v
                                 WorkflowLearner
                                         |
                                         v
                  RecipeService -> Codex structured review
                         |
                         v
             explicit target_instance dry-run preview
                         |
                         v
              CommandResponse + DiffReport + diagnostics
```

The session, learner, recipe, task, and diagnostics services are product-neutral. NX syntax stays in
`plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`; PowerMill syntax stays in
`cam_automation/adapters/powermill_macro.py` and its profile. Neither parser imports the other.

`events.jsonl` follows the repository's existing `ActivityEvent v1` fields. Shared actions use a `cam.*` prefix and PowerMill-only actions use `powermill.*`; the original macro operation remains an additive recipe field for display and audit.

## Learning Model

The MVP uses deterministic local induction instead of a remote model:

1. Parse explicit `# session: name` markers, timestamped macro lines, or JSONL events.
2. Normalize whitespace while preserving quoted PowerMill literals.
3. Group sessions by a structural signature that abstracts strings and numbers.
4. Select the dominant sequence shape.
5. Convert literals that vary at the same step across sessions into typed parameters.
6. Retain source line numbers and assign `safe`, `review`, or `blocked` risk.

This is explainable, fast, reproducible, and usable without an API key. A future LLM layer should propose names, intent, and higher-level recipe composition over this structured representation; it should not bypass the deterministic risk gate.

## Safety Boundary

- The base application has no installed CAM business capabilities.
- Installation is explicit and local; dependencies and requested permissions are visible first.
- The recorder may detect a running CAM process but does not attach without a registered,
  target-version native transport.
- Generated macros are artifacts, not execution requests.
- Command text is independently checked even when its submitted risk label is `safe`.
- Live and review-classified requests require a reviewed recipe, target version, test project
  snapshot, and identified approver.
- Live requests require an explicit runtime `target_instance_id`; automatic foreground targeting is
  not allowed because multiple CAM windows may be open.
- Product, instance ID, target version, project ID, and recipe hash are revalidated immediately
  before transport use and again against the response.
- External writes, project saves, exports, and NC output are `review`.
- Delete, quit, project reset/close, nested macro execution, and external process commands are `blocked`.
- Encoded command variants, Codex injection, Journal live calls, machine-control vocabulary,
  NC/G-code, and postprocess output fail closed before transport use.
- String parameters reject control characters and escape the learned quote delimiter.
- Real execution must add project snapshots, version checks, command acknowledgements, audit records, and an operator confirmation gate.
- Machine-ready NC and postprocessing output are rejected by the execution gateway.
- Simulation, collision, and shop-approval gates remain `not_run` or `required` in every fixture
  DiffReport.

## PowerMill Adapter, Phase 2

The Autodesk examples expose two practical transports:

```text
External automation host
  Autodesk.ProductInterface.PowerMILL.PMAutomation
  -> DoCommand / DoCommandEx / RecordMacro

In-process PowerMill add-in
  IPowerMILLPlugin + PluginFramework
  -> PluginServices.QueueCommand / DoCommandEx
```

The future adapter should implement a narrow contract:

```text
connect(version, instance_policy)
query(command) -> response
queue(reviewed_command)
record_macro(path)
get_project_snapshot() -> immutable audit data
```

Keep this transport out of the learner. This allows the same reviewed recipe to target a test double, an external automation process, or an in-process add-in.

## Performance

Log parsing is a single pass over input lines. Session signature construction and aligned literal
inference are linear in the selected command count and bounded by candidate/shape limits.

Release smoke on the bundled Windows runtime (2026-08-24):

| Path | Result |
|---|---:|
| 100,000 event SQLite import | 5.489 s |
| Filtered query, 200 runs | p95 0.404 ms |
| 10,000 sessions / 30,000 events build | 0.802 s |
| 10,000-session learning | 1.021 s, deterministic under reversed input |
| 1,000 same-instance command tasks | 0.770 s, ordered, max concurrency 1 |

These are offline fixture measurements, not production CAM latency or a customer workstation
capacity guarantee.
