# Host bridge usage

## NXOpen read-only export

`NxOpenSnapshotExporter` is an in-host NX journal adapter. It uses the
documented Siemens NXOpen Python surface:

- `NXOpen.Session.GetSession()`
- `session.Parts.Work`
- `work_part.Bodies`, `body.Tag`, and `body.Name`
- `work_part.PartUnits`
- `session.ReleaseNumber`

It only enumerates the work-part body inventory. Material and machine context
are operator inputs; this adapter does not infer them. Body `Tag` values are
session-level identifiers and are not stable across an NX close/reopen.
`object_inventory_digest` is not a geometry digest and `geometry_complete` is
always `false`; shape changes that preserve the body inventory can therefore
remain undetected. Every capture includes `host_evidence.capture_nonce` and
`captured_at` so an old capture is not silently reused.

From a shell associated with the installed NX environment, launch the journal
through the NX-provided runner:

```text
<NX install>\NXBIN\run_journal.exe -python C:\path\to\repo\host_bridges\nxopen_export_journal.py --instance-id nx:operator-session --project-id C:\parts\demo.prt --material P20 --units mm --output C:\exports\demo-snapshot.json
```

The exact `run_journal.exe` location and Python environment are installation
specific and must be checked on the customer's NX release. When `--units` is
omitted, the journal reads `PartUnits`; it never assumes millimetres. The
result is a `host_read_only` snapshot with `captured_in_host=true` and
`live_connection=false`, not a persistent connection lease.

This repository does not include NXOpen, and the local test suite uses only a
fixture-shaped fake module. A successful local test is not evidence of a real
NX installation or SDK compatibility.

## PowerMill status

`PowerMillComReadOnlyExporter` accepts an already selected COM object and the
official `PMAutomation` .NET type (for example, supplied by pythonnet). It
constructs `PMAutomation(dynamic powerMillComObject)` and reads only
`Version`, `ProcessId` (backed by `Debug.ProcessId`), and `Units`. It never
uses `UseExistingInstance`, `CreateNewInstance`, `DoCommand`, `Execute`, or
`Quit`. This is a small host identity/unit export, not a complete CAM
context export. Entity enumeration is returned as `unavailable` unless a
reviewed external provider supplies an object inventory; no guessed entity
command is issued.

For a Windows C# entry point that attaches to one exact running instance:

```powershell
.\host_bridges\powermill_attach_readonly.ps1 `
  -PMAutomationAssembly 'C:\PowerMillApi\Delcam.ProductInterface.PowerMILL.dll' `
  -TargetProcessId 4812 -InstanceId 'powermill:4812' -ProjectId 'demo-cavity' `
  -Material P20 -Output 'C:\exports\pm-snapshot.json' `
  -CompilerPath 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
```

The C# source first calls the documented static
`PMAutomation.GetListOfPmComObjects()`, matches `Debug.ProcessId` against
`--target-pid`, and only
then invokes the COM-object constructor. A missing PID fails; it never creates
or selects a different PowerMill process. `CompilerPath` is optional; without
it the script searches the installed Framework64/Framework v4.0.30319
`csc.exe`. The exact assembly path, .NET runtime, and API version must be
supplied by the customer installation.

The Autodesk `PowerShapeAndPowerMillAPI` MIT repository is the API reference
for this adapter (`PMAutomation.cs`, `PMAutomationTest.cs`). Runtime capture is
marked `verification_status=runtime_observed`, `verified=false`,
`captured_in_host=true`, and `live_connection=false`; imported JSON remains
unverified even when it contains host-evidence strings. Independent
verification is a separate customer acceptance step.
