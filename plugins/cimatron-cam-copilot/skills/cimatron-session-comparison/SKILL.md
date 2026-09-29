---
name: cimatron-session-comparison
description: Compare two or more Cimatron static Journal evidence sessions and separate repeated observations from unresolved or version-specific behavior.
metadata:
  version: "0.1.0"
---

# Cimatron Session Comparison

Compare only structured events produced by the Cimatron static adapter. Require
matching product identity and retain session IDs, source lines, target versions,
opaque arguments, and blocked actions.

Report common calls, differing arguments, missing calls, and confidence as
observed evidence rather than success probability. Do not promote repeated
observations to approved parameters or executable actions.

Keep the result review-first and dry-run. Never execute, replay, connect to a
host, generate source, emit NC/G-code, or suppress unresolved safety gates.
Human review, simulation, collision checks, and shop approval remain required
gates after comparison.
