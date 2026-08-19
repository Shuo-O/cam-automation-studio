---
name: nx-workflow-learning
description: "Analyze Siemens NX/UG NX Open Python journals or JSON action logs, identify repeated CAM workflows, and scaffold a review-first automation recipe."
---

# NX Workflow Learning

Use this skill when the user provides an NX journal, a journal recording, or a structured action log and asks to learn, generalize, or automate a repeated workflow.

## Workflow

1. Preserve the original journal. Do not import or execute it during analysis.
2. Locate `scripts/ugcam.py` from the plugin root, run `doctor`, and ingest at least two independently recorded `.py` journals.
3. Normalize actions into the shared `ActivityEvent` contract. Do not infer geometry or manufacturing intent from names alone.
4. Find repeated contiguous sequences with support measured across sessions. Treat changing values (part names, operation names, feeds, tolerances, and tool numbers) as parameter candidates instead of hard-coding them.
5. Present a reviewed recipe with:
   - preconditions and selected objects;
   - ordered NXOpen calls;
   - parameters and their source values;
   - confidence/support based on observed repetitions;
   - unresolved API calls or selections that require a human review.
6. Generate a Python NXOpen draft only after the user can see the recipe. Generated code must remain dry-run until implemented against the local NX stubs.

Before implementing mutating handlers, read [NX Open playbook](references/nx-open-playbook.md) and [production safety](references/production-safety.md). Use [workflow review template](references/workflow-review-template.md) when a formal review record is useful.

## Safety and quality

- Journals are evidence of one session, not proof that a recipe is valid for every part.
- Keep selection and coordinate references visible; do not silently replace them with guessed object lookups.
- Resolve recorded object IDs through stable names, attributes, PMI, or verified geometry queries.
- Prefer deterministic, idempotent steps and require confirmation before changing the active part or writing files.
- Keep the original journal available for comparison and regression tests.
- Do not generate or send machine-ready NC code. Require toolpath status checks, collision/gouge verification, machine simulation, postprocessor review, and explicit human approval.
- If the input is a PowerMill `.mac` file, use the PowerMill workflow Skill instead of changing the NX parser.
