# Production safety gates

Apply these gates before a learned workflow can mutate a production part.

- Work on a versioned test copy; never silently overwrite the active part.
- Resolve machine, stock, fixtures, MCS, material, tool and holder explicitly.
- Enforce units and parameter bounds.
- Stop when a selected object is missing or ambiguous.
- Stop when an operation is dirty, failed, or has no generated toolpath.
- Run gouge and collision checks.
- Run machine simulation with the approved machine kit.
- Keep postprocessing and NC release under the shop's existing controls.
- Record the recipe version, NX version, inputs, reviewer and verification evidence.
- Require an identified human approver.

Log learning can rank automation opportunities; it does not prove machining correctness.

