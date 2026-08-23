---
name: powermill-cam-workflow-learning
description: Learn, audit, and parameterize repeatable Autodesk PowerMill workflows from macro files, command logs, or JSONL operator traces, then generate a review-first recipe, report, and macro. Use for offline workflow capture and automation design; do not use it to execute commands in a live PowerMill project.
metadata:
  version: "0.2.0"
---

# PowerMill Workflow Learning

Turn observed operator commands into review evidence while keeping all host
execution outside the skill boundary.

## Workflow

1. Confirm the input is a PowerMill macro, command log, or JSONL trace. Preserve the source file.
2. Prefer at least two comparable sessions and add `# session: <name>` markers when the boundaries are known. A single session is valid but cannot prove which literals are parameters.
3. From the repository root, run the offline wrapper:

   ```powershell
   python plugins/powermill-cam-copilot/skills/powermill-cam-workflow-learning/scripts/learn_powermill_log.py <log-path> --output <artifact-directory>
   ```

4. Inspect `report.md` first. Explain unmatched sessions, inferred parameters, review steps, and blocked steps.
5. Inspect `workflow.mac` as a review artifact only. Do not send it to a host.
6. Keep `review` steps commented and `blocked` steps disabled.

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

Product input is parsed only by
`cam_automation/adapters/powermill_macro.py`. Read
[PowerMill integration](references/powermill-integration.md) for the fixture
transport boundary. Queries remain read-only and previews remain dry-run.

Never emit or request machine-ready NC, G-code, postprocessing, or machine
control. Human review, CAM simulation, collision checks, and shop approval are
all mandatory and cannot be inferred from a successful fixture.
