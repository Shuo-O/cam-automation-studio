# PowerMill Recipe Review Contract

Use `cam.codex.bridge.v1` JSON.

Each finding contains `finding_id`, `severity`, `code`, `message`, `step_ids`,
and `evidence_refs`. Each parameter suggestion contains `name`,
`proposed_value`, `rationale`, `confidence`, and `evidence_refs`. Each question
contains `question_id`, `text`, and `required`.

Always retain human recipe review, target version validation, CAM simulation,
collision checks, and shop approval. Reject live execution, NC, G-code,
postprocessing, machine control, credentials, raw absolute project paths, and
undeclared uploads. Review and preview remain dry-run.
