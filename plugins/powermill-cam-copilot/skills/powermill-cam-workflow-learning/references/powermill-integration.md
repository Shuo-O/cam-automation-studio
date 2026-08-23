# PowerMill Adapter and Fixture Boundary

Use only `cam_automation/adapters/powermill_macro.py` for product syntax. It
provides the offline parser, `PowerMillTransport` protocol, and
`FixturePowerMillTransport`.

- Bind every request to product, target version, instance, project snapshot, and
  reviewed recipe hash.
- Use read-only queries or fixture dry-run previews.
- Keep instance responses isolated and fail closed on disconnect, timeout, target
  mismatch, or unsupported capability.
- Treat `safe`, `review`, and `blocked` as review classifications, not permission
  to execute.
- Mark simulation, collision checking, and shop approval `required` or `not_run`
  unless independent evidence is supplied.

Do not attach to a live host, issue live commands, register an add-in, upload
project data, or create NC, G-code, postprocessing, or machine-control output.
