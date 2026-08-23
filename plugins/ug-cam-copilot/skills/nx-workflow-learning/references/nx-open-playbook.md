# NX Open Static Review Playbook

Use this reference while reviewing mined NX evidence.

1. Preserve the selected Journal and parse it only with
   `src/ugcam_ai/adapters/nx_journal.py`.
2. Confirm the target release and inspect its selected
   `UGOPEN/pythonStubs` through `src/ugcam_ai/versioning.py`.
3. Keep Builder creation, setters, Commit, Destroy, and Undo Mark evidence until
   their role is reviewed.
4. Flag every recorded `FindObject` identifier. Suggest attributes, controlled
   names, PMI, or verified geometry queries without claiming uniqueness.
5. Report selected objects, units, frames, tools, holders, stock, and fixtures as
   unresolved when the evidence does not establish them.
6. Use `src/ugcam_ai/transport.py` only for read-only queries or an offline
   fixture dry-run. Do not execute a Journal.

Do not create postprocessing, NC, G-code, or machine-control output.

