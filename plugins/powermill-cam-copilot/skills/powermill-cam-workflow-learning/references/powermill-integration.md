# PowerMill Integration

Read this reference only when the task moves beyond offline log learning into a real PowerMill adapter or add-in.

## Supported Shapes

Autodesk documents a Visual Studio and .NET Framework 4.8 automation path:

- [Autodesk support article](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/How-to-automate-Powermill-using-Api-libraries.html)
- [PowerShape and PowerMill API](https://github.com/Autodesk/PowerShapeAndPowerMillAPI)
- [PowerMill API examples](https://github.com/Autodesk/powermill-api-examples)

External automation can use `Autodesk.ProductInterface.PowerMILL.PMAutomation` to attach to or create an automation instance and issue `DoCommand` / `DoCommandEx`.

An in-process add-in implements the PowerMill plugin interfaces, uses the installed `PluginFramework.dll` and generated `PowerMill.dll`, and sends commands through the plugin services token. Autodesk's example build requires COM registration and Plugin Manager enablement.

## Adapter Requirements

Before building or running:

- identify the exact installed PowerMill version and architecture;
- obtain dependencies from that installation or its supported package;
- use a unique plugin GUID;
- build with the framework version supported by the target release;
- register only the built test assembly;
- use a disposable or snapshotted PowerMill project;
- keep command query, command queue, and macro recording behind a narrow transport interface;
- log command, response, duration, target version, recipe hash, and operator approval.

Do not download an arbitrary prebuilt `PowerMill.dll`, reuse another plugin GUID, register a guessed assembly, or test generated NC output on an unsnapshotted production project.

## Execution Gate

The offline recipe risk classification remains authoritative:

- `safe`: eligible for a test run after the complete macro is reviewed.
- `review`: requires explicit operator approval and a declared output path.
- `blocked`: must not be sent by the adapter.

Query current project state before each run and fail closed when the PowerMill version, project identity, expected entities, or recipe hash differs from the reviewed plan.
