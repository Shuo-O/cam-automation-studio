---
name: powermill-cam-workflow-learning
description: Learn, audit, and parameterize repeatable Autodesk PowerMill workflows from macro files, command logs, or JSONL operator traces, then generate a review-first recipe, report, and macro. Use for offline workflow capture and automation design; do not use it to execute commands in a live PowerMill project.
---

# PowerMill Workflow Learning

Turn observed operator commands into a reproducible automation artifact while keeping production execution outside the skill boundary.

## Workflow

1. Confirm the input is a PowerMill macro, command log, or JSONL trace. Preserve the source file.
2. Prefer at least two comparable sessions and add `# session: <name>` markers when the boundaries are known. A single session is valid but cannot prove which literals are parameters.
3. From the repository root, run:

   ```powershell
   python plugins/powermill-cam-copilot/skills/powermill-cam-workflow-learning/scripts/learn_powermill_log.py <log-path> --output <artifact-directory>
   ```

4. Inspect `report.md` first. Explain unmatched sessions, inferred parameters, review steps, and blocked steps.
5. Inspect `workflow.mac` against the target PowerMill version and the operator's intended project state. Never represent generated syntax as production-validated without a PowerMill test.
6. Keep `review` steps commented unless the user explicitly approves them for a test project. Never activate `blocked` steps through this skill.

## Inputs

- Plain PowerMill command or macro lines.
- Timestamped lines such as `[timestamp] PowerMill> <command>`.
- JSONL objects with `command` or `macro`, plus optional `timestamp` and `source`.
- Use `# session: name` or `// session: name` to delimit comparable runs.

The parser ignores blank lines, comments, and ordinary debug noise. It keeps source line numbers for audit.

## Output Contract

- `recipe.json`: versioned steps, inferred parameters, source lines, and risk.
- `report.md`: match statistics, parameters, sequence, and diagnostics.
- `workflow.mac`: safe lines active, review lines commented by default, blocked lines always commented.

Do not silently edit the source trace. When the structural sequences differ, report that the dominant session group was selected rather than merging unrelated procedures.

## Integration Boundary

For a real PowerMill connection, in-process add-in, supported Autodesk libraries, or target-version prerequisites, read [references/powermill-integration.md](references/powermill-integration.md). Live execution is a separate implementation task requiring explicit authorization and a test project snapshot.
