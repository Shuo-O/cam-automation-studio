# NX Session Comparison Contract

- Input sessions: 2 to 5 unique IDs, one product, one instance per session.
- Parser: `src/ugcam_ai/adapters/nx_journal.py`.
- Shared comparison: `cam_automation/sessions.py`.
- Target evidence: exact NX version and selected local stubs when available.
- Output: v1 `SessionDiff` with a baseline that belongs to `session_ids`.
- Durations: non-negative milliseconds for every session.
- Unknown selectors, units, frames, or operation state remain unresolved.

Session similarity is evidence for review. It is not approval for simulation or
production. Any promoted recipe remains dry-run and still requires review by an
identified human, CAM simulation, collision checks, and shop approval.
