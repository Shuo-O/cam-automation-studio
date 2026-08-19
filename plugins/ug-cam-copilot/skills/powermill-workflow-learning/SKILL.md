---
name: powermill-workflow-learning
description: "Learn repeatable Autodesk PowerMill workflows from recorded .mac files or JSON logs and produce reviewed automation drafts."
---

# PowerMill Workflow Learning

Use this skill for PowerMill macros, recorded command logs, or structured event traces. The local MVP is available at `app/powermill_ai_demo.py` and can be started with `scripts/run-demo.ps1`.

## Workflow

1. Keep the source `.mac` or log unchanged. Record the PowerMill version and project/setup context when available.
2. Parse each command into `action`, `target`, `value`, and raw source text. Ignore comments and blank lines.
3. Compare contiguous command sequences while ignoring values for matching. Mark values that vary between occurrences as parameters.
4. Return a recipe with support, confidence, preconditions, parameter candidates, and the exact source commands used as evidence.
5. Export a `.mac` draft only after review. Preserve exact recorded lines where safe; use `TODO` comments for mappings that cannot be proven from the log.

## Guardrails

- Never run a generated macro automatically. PowerMill macros can change toolpaths, stock, machine settings, or output files.
- Do not claim that a command is valid across PowerMill releases without checking the local Macro Programming Guide.
- Treat selection names and dialog state as fragile. Prefer stable identifiers and explicit preconditions in a later adapter.
- Validate on a copy of the project and compare toolpath, collision, and cycle-time results before production use.

## Output

When asked to automate a workflow, provide:

- a concise recipe and the evidence sequence;
- variable values and suggested parameter names;
- a reviewed PowerMill `.mac` draft;
- unresolved assumptions and a validation checklist.
