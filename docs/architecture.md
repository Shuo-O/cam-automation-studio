# Architecture

## CAM Automation Studio

The repository now exposes one local module for both products:

```text
UG/NX Journal ----> NX static adapter -----+
PowerMill macro --> PowerMill profile -----+--> unified analysis result
ActivityEvent JSONL -> shared adapter -----+        |
                                                   +--> recipe + preview
                                                   +--> Codex Bridge v1
                                                   +--> human review record
```

`cam_automation/integrations.py` is the product-facing service boundary. It keeps the NX parser in
`plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py` and the PowerMill vocabulary in
`cam_automation/profiles/powermill.py`. The web server and UI consume the same response shape for
both products. Product-only actions remain prefixed with `nx.*` or `powermill.*`; shared operations
use `cam.*`.

`cam_automation/codex_bridge.py` implements `cam.codex.bridge.v1`. It exports evidence and review
instructions, validates an operator/Codex review response, and can persist an exchange bundle. It
does not execute returned text or attach to a CAD/CAM process.

## Data Flow

```text
PowerMill macro / command log / JSONL
                  |
                  v
        generic streaming parser
                  |
                  v
       normalized command events
                  |
          +-------+-------+
          |               |
          v               v
  PowerMill profile   sequence learner
  operation + risk    dominant shape +
          |           varying literals
          +-------+-------+
                  |
                  v
        reviewed workflow recipe
          |        |               |
          v        v               v
    events.jsonl report.md     workflow.mac
```

The parser and sequence learner are CAM-neutral. `cam_automation/profiles/powermill.py` owns the current product vocabulary and safety rules. An NX profile can reuse the event, recipe, parameter, report, CLI, and UI layers.

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

- The demo never discovers or attaches to a running PowerMill process.
- Generated macros are artifacts, not execution requests.
- External writes, project saves, exports, and NC output are `review`.
- Delete, quit, project reset/close, nested macro execution, and external process commands are `blocked`.
- String parameters reject control characters and escape the learned quote delimiter.
- Real execution must add project snapshots, version checks, command acknowledgements, audit records, and an operator confirmation gate.

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

Log parsing is a single pass over input lines. Session signature construction and aligned literal inference are linear in the selected command count. The included test parses 20,000 commands under a conservative three-second ceiling on the bundled local runtime.
