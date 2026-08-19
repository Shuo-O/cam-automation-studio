# PowerMill parallel integration

The current PowerMill browser demo stays intact. A future in-process adapter can also feed the high-performance event store without changing the NX parser:

1. Record a PowerMill macro or observe commands through the existing PowerMill development path.
2. Convert each command into one JSON object matching `schemas/activity-event.schema.json`.
3. Use `product: "powermill"` and keep sequence numbers contiguous within a session.
4. Reuse canonical `cam.*` actions where semantics match NX; use `powermill.*` for product-only behavior.
5. Import the result with `ugcam.py ingest events.jsonl --product auto`.

Recommended shared canonical actions:

| Meaning | Canonical action |
|---|---|
| Select program/tool/method/geometry context | `cam.group.select.<kind>` |
| Create a machining operation/toolpath | `cam.operation.create` |
| Change a strategy parameter | `cam.parameter.set.<name>` |
| Calculate a toolpath | `cam.toolpath.generate` |
| Collision/gouge verification | `cam.verify.run` |
| Postprocess approved output | `cam.output.postprocess` |

For a native `.mac` adapter, add `src/ugcam_ai/adapters/powermill_macro.py` and its own fixtures. Do not modify `nx_journal.py`.

Official references:

- [Autodesk PowerMill macros](https://help.autodesk.com/view/PWRM/2025/ENU/?guid=PWRM-MACROS-MACROS)
- [Recording macros in PowerMill](https://help.autodesk.com/cloudhelp/2022/ENU/PWRM-ReferenceHelp/files/GUID-8B22DBDB-509A-4E66-8AD4-D06BA6720DFC.htm)
- [Starting PowerMill with `-macro`](https://help.autodesk.com/cloudhelp/2022/ENU/PWRM-ReferenceHelp/files/GUID-C81DEB55-D48B-45A4-A103-CAE1952F01D7.htm)

