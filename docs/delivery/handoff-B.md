# Handoff B: Manufacturing Context and Read-only Bridges

Date: 2026-09-29

## Delivered interfaces

- `ManufacturingContextService(root: Path)`
  - `import_snapshot(payload) -> dict`
  - `list_snapshots() -> list`
  - `get_snapshot(snapshot_id) -> dict`
  - `resolve_selector(snapshot_id, selector) -> dict`
  - `capabilities() -> dict`
- `resolve_snapshot_selector(snapshot, selector) -> dict` is a pure helper for
  case/proposal services. It returns `resolved`, `ambiguous`, or `unresolved`
  and never selects the first candidate.
- `NxOpenSnapshotExporter.export_snapshot(...)` is a read-only NXOpen journal
  entry. `host_bridges/nxopen_export_journal.py` is the command-line journal
  wrapper.
- `PowerMillReadOnlyBridge` is intentionally only a provider boundary. It does
  not claim to export a PowerMill project or load COM/.NET.
- `PowerMillComReadOnlyExporter` attaches to a caller-selected COM object and
  reads only Version/PID/Units through the official PMAutomation wrapper.

## Storage and integrity

Snapshots are JSON files under `<root>/snapshots`, written with a temporary
file, fsync, and `os.replace`. IDs are portable `snapshot-<hash>` names.
Re-importing the same ID and content is idempotent; reusing an ID for different
content is rejected. Fixture input without `fixture_id` gets a deterministic
`fixture-<hash>` identifier, so the frozen customer fixture remains accepted.

The content hash covers product, instance/project/version, units, material,
machine, objects, object extensions, object hashes, optional stock/tools/
fixtures/geometry digest, host evidence, and retained extensions. Object hashes
are calculated after all object fields are retained. On read, snapshot and
object hashes are rechecked; corruption raises `ValueError`.

## NX host entry and evidence

From the target NX installation, invoke:

```text
<NX install>\NXBIN\run_journal.exe -python C:\path\to\repo\host_bridges\nxopen_export_journal.py --instance-id nx:operator-session --project-id C:\parts\demo.prt --material P20 --units mm --output C:\exports\demo-snapshot.json
```

The adapter calls the documented `NXOpen.Session.GetSession()`,
`session.Parts.Work`, `work_part.Bodies`, `body.Tag`, `body.Name`,
`work_part.PartUnits`, and `session.ReleaseNumber` properties. It does not
commit, queue, execute, postprocess, or emit NC. Material and machine are
operator-supplied.

`object_inventory_digest` is only a body inventory digest; `geometry_complete`
is `false`. Body tags are session-level identifiers and are not stable across
NX close/reopen. Each host capture carries `capture_nonce` and `captured_at`.
The persisted record is marked `captured_in_host=true`, `connection_status=
captured`, and `live_connection=false`; this is not a live connection lease.

NXOpen is not installed in this workspace. Tests inject a fixture-shaped fake
module and therefore do not constitute real NX validation. The exact journal
runner path, release, Python ABI, and API compatibility require customer-host
verification.

## PowerMill read-only attachment

`PowerMillComReadOnlyExporter` accepts a COM object selected by the caller and
the official `PMAutomation` .NET type. It wraps the selected object with
`PMAutomation(dynamic powerMillComObject)` and reads `Version`, `ProcessId`
(`Debug.ProcessId`), and `Units`. It does not invoke `UseExistingInstance`,
`CreateNewInstance`, `DoCommand`, `Execute`, or `Quit`. The accompanying C#
and PowerShell entry point calls
`PMAutomation.GetListOfPmComObjects()`, matches one exact PID, and fails closed
when no match exists. It never creates a host.
The PowerShell parameter is `-TargetProcessId` (the C# argument is
`--target-pid`, avoiding the Windows built-in `$PID` collision); compiler
lookup falls back to the installed Framework64/Framework v4.0.30319
`csc.exe`, or accepts an explicit `-CompilerPath`.

The supported export is limited to host identity (version/PID), units, and
capture evidence; it is not a complete CAM context export. The reviewed API
surface does not establish a version-stable, side-effect-free entity
enumeration contract for this adapter. The exporter therefore returns an
explicit `object_inventory_status=unavailable` unless a separately reviewed
external provider supplies the inventory; it never invents an object list.

References:

- Autodesk, `Delcam.ProductInterface.PowerMILL/PMAutomation.cs` (MIT):
  `https://github.com/Autodesk/PowerShapeAndPowerMillAPI/blob/master/Delcam.ProductInterface.PowerMILL/PMAutomation.cs`
- Autodesk, `Delcam.ProductInterface.PowerMILL.Test/PMAutomationTest.cs`:
  `https://github.com/Autodesk/PowerShapeAndPowerMillAPI/blob/master/Delcam.ProductInterface.PowerMILL.Test/PMAutomationTest.cs`

Runtime captures are marked `verification_status=runtime_observed`,
`verified=false`, `captured_in_host=true`, and `live_connection=false`.
Importing JSON never upgrades those fields to independent verification.

## Tests

Using the isolated Python environment:

```text
%TEMP%\cam-assessment-20260929-venv\Scripts\python.exe -m unittest -v tests/test_manufacturing_context.py tests/test_customer_api.py
```

Result: 15 tests passed. This includes persistence restart, hash mutation,
duplicate/ambiguous/unresolved selectors, malformed input `ValueError`,
corruption detection, fixture ID generation, NX fixture-shape export, and the
customer HTTP round trip. No test claims a real NX or PowerMill installation.

The C# entry was also compiled with the .NET Framework v4 `csc.exe` against a
temporary test-only PMAutomation stub and run without a host. It resolved the
stub from the temporary API directory and failed closed with
`No running PowerMill COM object matched --target-pid.` The stub is not part of
the repository or customer delivery.
