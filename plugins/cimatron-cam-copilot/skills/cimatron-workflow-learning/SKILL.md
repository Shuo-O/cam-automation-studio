---
name: cimatron-workflow-learning
description: Analyze Cimatron 2026 Journaling Python or C# source offline, preserve opaque evidence, and produce a review-first recipe. Never execute source or connect to a host.
metadata:
  version: "0.1.0"
---

# Cimatron Workflow Learning

Use only a user-selected Cimatron Journal file or structured Cimatron events. Keep the
source local and unchanged.

1. Parse source through `cam_automation/adapters/cimatron_journal.py`; never import,
   evaluate, compile, replay, or execute the source.
2. Preserve source lines, target-version evidence, call arguments, and unknown
   expressions as opaque values. Do not infer unsupported SDK semantics.
3. Mark dynamic execution, external processes, postprocessing, G-code, NC, CLSF,
   DNC, and machine output as blocked.
4. Return a v1 recipe with static evidence, unresolved parameters, and required
   review, simulation, collision, and shop-approval gates.
5. Keep Cimatron limited to static Journal analysis. Flow import, live host
   connections, command tasks, semantic editing, and production actions are
   unavailable.

All previews are dry-run evidence reports. Never emit machine-ready NC, G-code,
postprocessing output, or machine-control instructions. Keep human review,
simulation, collision checks, and shop approval unresolved until separately
verified.
