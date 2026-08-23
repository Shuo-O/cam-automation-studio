---
name: powermill-recipe-review
description: Review a structured Autodesk PowerMill CAM recipe against source events, session differences, target bindings, and mandatory simulation and shop gates.
metadata:
  version: "0.2.0"
---

# PowerMill Recipe Review

Review a v1 recipe without sending commands to a host.

1. Require `product=powermill`, a valid recipe hash, target version, target
   instance, project snapshot, source events, and explicit questions.
2. Check product evidence and adapter payloads only against
   `cam_automation/adapters/powermill_macro.py`.
3. Parse review input and output with `cam_automation/codex_bridge.py`; never
   infer structured findings from prose.
4. Return structured findings, parameter suggestions, questions, summary,
   request ID, reviewer, timestamp, and mandatory gates.
5. Reject blocked actions, target mismatches, live execution requests, and
   machine-ready output.

Read [review contract](references/review-contract.md). An
`approved_for_simulation` result is not production approval.
