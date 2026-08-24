---
name: nx-session-comparison
description: Compare two to five Siemens NX CAM activity sessions, explain common steps and parameter differences, and prepare evidence for review-first recipe mining.
metadata:
  version: "0.3.0"
---

# NX Session Comparison

Use this skill for selected NX event sessions that already conform to the shared
`ActivityEvent` contract.

1. Accept evidence only from
   `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py` or structured
   NX events. Never execute a Journal.
2. Confirm every event is `product=nx` and keep sessions separated by instance
   and project.
3. Compare two to five sessions through the shared `SessionService`; report
   common steps, missing/extra steps, parameter differences, and duration.
4. Preserve `event_session_id + seq` evidence references and distinguish
   recorded facts from suggestions.
5. Return a `SessionDiff` only. Recipe promotion belongs to a separate review.

Read [comparison contract](references/comparison-contract.md) for validation and
output rules. Never emit NC, G-code, postprocessing, or machine-control output.
