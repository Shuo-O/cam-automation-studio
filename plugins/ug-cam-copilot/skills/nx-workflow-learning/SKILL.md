---
name: nx-workflow-learning
description: Analyze Siemens NX Open Python journals or NX activity JSON offline, identify repeated CAM workflows, and produce a review-first recipe. Use for static learning, never live Journal execution.
metadata:
  version: "0.3.0"
---

# NX Workflow Learning

Learn only from selected NX journals or structured NX events. Keep the source local and
unchanged. This skill does not connect to or execute inside a live CAM project.

## Workflow

1. Parse journals only through
   `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`. Never import,
   execute, or evaluate a journal.
2. Check the target release against
   `plugins/ug-cam-copilot/src/ugcam_ai/versioning.py` and selected local stubs.
3. Normalize evidence into the shared `ActivityEvent` contract, preserving source
   references and recorded selector risks.
4. Compare at least two sessions when available. Treat varying observed values as
   candidates, not approved parameters.
5. Return a v1 recipe with support, typed parameters, source references, blocked
   steps, and every mandatory gate unresolved.
6. Keep any preview behind
   `plugins/ug-cam-copilot/src/ugcam_ai/transport.py` in read-only or fixture
   dry-run mode.

Read [NX Open playbook](references/nx-open-playbook.md) before interpreting
recorded APIs, [production safety](references/production-safety.md) for all
review gates, and [workflow review template](references/workflow-review-template.md)
when producing a formal review record.

Never emit machine-ready NC, G-code, postprocessing output, or machine-control
instructions. A recipe remains review-first until an identified human reviews it
and separate CAM simulation, collision checks, and shop approval are complete.
