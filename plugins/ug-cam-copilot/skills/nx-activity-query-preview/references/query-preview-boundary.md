# NX Query and Preview Boundary

Allowed:

- read-only metadata queries through `src/ugcam_ai/transport.py`;
- offline `FixtureNxTransport` responses;
- dry-run recipe previews bound to version, instance, project, and recipe hash;
- structured before/after snapshot references.

Rejected:

- Journal execution, imports, `exec`, or evaluation;
- unbound or mismatched targets;
- blocked recipe steps;
- NC, G-code, postprocessing, or machine-control content;
- claims that fixture success proves simulation, collision, or shop approval.
