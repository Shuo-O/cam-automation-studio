# Shared AI Context

## Default Collaboration Model

- Codex App and Claude Code may work in the same local working directory.
- Codex is the primary implementer by default.
- Claude Code is primarily the reviewer, architect, debugger, or second opinion by default.
- The user is the final coordinator and decides when work is ready.

## Required Project Bootstrap

- At the start of work in any new project or task directory, check whether these files exist:
  - `AGENTS.md`
  - `CLAUDE.md`
  - `docs/AI_SHARED_CONTEXT.md`
- If any are missing, run `ai-shared-init` from the project root before substantive work.
- If `ai-shared-init` is unavailable, create equivalent files manually using this shared context.

## Coordination Rules

- Only one agent should write files at a time.
- Before editing, state which files are expected to change.
- Do not overwrite uncommitted changes from the user or another agent.
- Use `git status --short` before substantial edits.
- Use `git diff` after substantial edits and before handing work off.
- Prefer read-only review when another agent is actively editing.
- Keep unrelated refactors out of the current task.
- If unexpected file changes appear, treat them as user or other-agent work and do not revert them without explicit permission.

## Verification

- Run the relevant tests, type checks, linters, or build commands after code changes.
- If verification cannot run, explain exactly why and what remains unverified.
- Do not claim success without verification or a clear reason verification was skipped.

## Handoff Format

When handing work to the other agent, include:

- Goal
- Files touched
- Current status
- Commands run
- Verification result
- Open questions or risks

## Git Discipline

- Keep changes small enough to review.
- Prefer separate commits for unrelated concerns.
- Do not stage, commit, reset, checkout, or revert changes unless the user asks.
- For parallel implementation experiments, prefer separate git worktrees instead of two agents editing the same checkout.

## Company Git Commit Convention

This convention is mandatory for company business repositories. Treat a repository as a company repository when its
Git remote uses a MyHexin/company GitLab host, including `106.54.188.197:18080`, `gitlab-outer.myhexin.com`, or another
company-owned `myhexin.com` Git host.

Before creating or amending a commit in a company repository:

1. Read the project `AGENTS.md` and `docs/AI_SHARED_CONTEXT.md` for a fixed task ID.
2. If no fixed task ID is recorded, use a task ID explicitly supplied by the user or present in validated project
   metadata such as the branch name or an existing server-accepted commit.
3. Never invent a Jira, CRM, or `dsw-` task ID. If no validated ID is available, ask the user once before committing.
4. Validate the final subject and body before pushing. If the server rejects the commit, amend it before retrying.
5. Project task IDs are repository-specific. Never carry an ID such as `AIMEOMNI-4` from one repository into another.

Every commit must use this format:

```text
{task-id} {type} {title}

{optional explanation}
```

- `task-id` must be a Jira task ID, CRM task number, or a `dsw-` prefixed sequence.
- `type` must be exactly one of: `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, `docs`, `test`, `ci`, `build`, `chore`, `skip`.
- A commit has exactly one type.
- Keep the title concise and do not use emoji or unnecessary special characters.
- Use the optional explanation for purpose and impact scope.
- Example: `TCLOUD-100 feat Provide branch validation`.
- Do not apply this convention to personal repositories on public hosts such as GitHub unless the project explicitly
  opts in.

## Company Jira Workflow

For company repositories, use the internal Jira instance at `http://jira.myhexin.com` and the existing authenticated
Jira connector or browser session. Before committing, provide the user with a concise summary of the changes, impact,
and verification results, and wait for approval. After approval:

1. Create or confirm an independent Jira issue for the current requirement.
2. Use issue type `Task` unless the user explicitly requests another type.
3. Add every newly created issue to the latest non-closed Sprint for its project, preferring the active Sprint over a
   future Sprint, and verify the assignment before treating issue creation as complete. If no non-closed Sprint exists,
   report that explicitly instead of assigning the issue to a closed Sprint.
4. Use the issue key in the commit subject and follow the company commit format.
5. After the commit, sync the commit hash, summary, and verification results back to that Jira issue.

Never reuse a Jira issue merely because it appears in the branch name, parent commit, or a previous requirement.
Never invent an issue key; if a new issue cannot be created or confirmed, ask the user before committing.

## Local Project Notes

- Add project-specific architecture, setup, and test commands to the project-local `docs/AI_SHARED_CONTEXT.md`.
- Add any files, directories, or workflows that agents should avoid changing.

## CAM Automation Studio local notes

- Python 3.10+; existing CLI and HTTP paths use the standard library.
- Optional MCP: `python -m pip install -e ".[mcp]"`, then `python -m cam_automation mcp`.
- Tests: `python -m unittest discover -s tests -v`; NX plugin tests:
  `python -m unittest discover -s plugins/ug-cam-copilot/tests -v`.
- External CAD metadata and configuration templates are in `cam_automation/cad_integrations.json`.
- Preserve repository dry-run / NC boundaries in AGENTS.md. Templates do not imply live connections.
