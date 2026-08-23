---
name: nx-recipe-review
description: Review a structured Siemens NX CAM recipe against source events, session differences, target stubs, and mandatory simulation and shop gates.
metadata:
  version: "0.3.0"
---

# NX Recipe Review

Review a v1 recipe without executing it.

1. Require `product=nx`, a valid recipe hash, target version, target instance,
   project snapshot, source events, and explicit questions.
2. Check product calls only against
   `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`, selected local
   stubs through `src/ugcam_ai/versioning.py`, and the read-only/dry-run
   `src/ugcam_ai/transport.py` boundary.
3. Parse review input and output with `cam_automation/codex_bridge.py`; never
   infer structured findings from prose.
4. Return structured findings, parameter suggestions, questions, summary,
   request ID, reviewer, timestamp, and mandatory gates.
5. Reject blocked actions, ambiguous recorded selectors, target mismatches,
   live execution requests, and machine-ready output.

Read [review contract](references/review-contract.md) before producing a result.
`approved_for_simulation` is not production approval.
