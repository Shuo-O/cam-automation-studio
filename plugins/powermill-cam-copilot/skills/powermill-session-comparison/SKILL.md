---
name: powermill-session-comparison
description: Compare two to five Autodesk PowerMill CAM activity sessions, explain common steps and parameter differences, and prepare evidence for review-first recipe mining.
metadata:
  version: "0.2.0"
---

# PowerMill Session Comparison

Use this skill for selected PowerMill sessions that conform to the shared
`ActivityEvent` contract.

1. Accept product evidence only from
   `cam_automation/adapters/powermill_macro.py` or structured PowerMill events.
2. Confirm every event is `product=powermill` and keep sessions separated by
   instance and project.
3. Compare two to five sessions through the shared `SessionService`; report
   common steps, missing/extra steps, parameter differences, and duration.
4. Preserve `event_session_id + seq` evidence references and distinguish facts
   from inferred parameters.
5. Return a `SessionDiff` only. Do not run a macro or host command.

Read [comparison contract](references/comparison-contract.md). Never emit NC,
G-code, postprocessing, or machine-control output.
