# PowerMill Session Comparison Contract

- Input sessions: 2 to 5 unique IDs, one product, one instance per session.
- Parser: `cam_automation/adapters/powermill_macro.py`.
- Shared comparison: `cam_automation/sessions.py`.
- Output: v1 `SessionDiff` with a baseline that belongs to `session_ids`.
- Durations: non-negative milliseconds for every session.
- Preserve command order, quoted whitespace, defaults, and source references.
- Unknown selection state or project prerequisites remain unresolved.

Session similarity is evidence for review, not permission to execute. Any
promoted recipe remains dry-run and still requires review by an identified
human, CAM simulation, collision checks, and shop approval.
