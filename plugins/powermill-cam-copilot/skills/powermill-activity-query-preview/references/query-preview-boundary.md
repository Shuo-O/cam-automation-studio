# PowerMill Query and Preview Boundary

Allowed:

- read-only queries through `PowerMillTransport`;
- offline `FixturePowerMillTransport` responses;
- dry-run recipe previews bound to version, instance, project, and recipe hash;
- structured before/after snapshot references.

Rejected:

- live command or macro dispatch;
- unbound or mismatched targets;
- blocked recipe steps;
- NC, G-code, postprocessing, or machine-control content;
- claims that fixture success proves simulation, collision, or shop approval.
