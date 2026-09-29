---
name: cimatron-recipe-review
description: Review a structured Cimatron static-evidence recipe against source lines, target-version assumptions, and mandatory CAM safety gates.
metadata:
  version: "0.1.0"
---

# Cimatron Recipe Review

Require `product=cimatron`, a valid recipe hash, source events, source references,
and an explicit review question. Keep the review dry-run only. Check that every step remains static evidence,
that opaque or unknown calls are not edited, and that blocked output operations
remain blocked.

Return structured findings, unresolved questions, evidence references, and the
review, simulation, collision, and shop-approval gates. An approval for
simulation is not production approval.

Never send commands, attach to Cimatron, execute a Journal, generate FlowGraph
actions, emit NC/G-code, or claim a safety gate passed without matching supplied
evidence. Human review, simulation, collision checks, and shop approval remain
separate required gates.
