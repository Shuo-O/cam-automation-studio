# PowerMill Automation Research

Checked on 2026-08-20.

## Official Autodesk Guidance

- [How to automate PowerMill and PowerShape using API libraries](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/How-to-automate-Powermill-using-Api-libraries.html) recommends a Visual Studio project, .NET Framework 4.8, and PowerMill/PowerShape NuGet references.
- [Autodesk/PowerShapeAndPowerMillAPI](https://github.com/Autodesk/PowerShapeAndPowerMillAPI) is Autodesk's MIT-licensed automation library. Its PowerMill root uses the `PowerMILL.Application` COM class, supports attaching or starting an automation instance, runs commands through `DoCommand` / `DoCommandEx`, and exposes macro recording.
- [Autodesk/powermill-api-examples](https://github.com/Autodesk/powermill-api-examples) is Autodesk's Apache-2.0 example set. It documents `PowerMill.dll`, `PluginFramework.dll`, COM registration, plugin category registration, and enabling an add-in through PowerMill Plugin Manager.
- The Drilling Automation example implements a PowerMill plugin pane and routes commands through `PluginServices.QueueCommand` and `DoCommandEx`. Autodesk notes that these examples have low maintenance priority, so target-version validation remains necessary.

No Autodesk source code is copied into this demo. The implementation uses the public integration shape and its documented command/macro concepts.

## Product Benchmark

[OptiNC for PowerMill](https://www.optinc.tech/optinc/?lang=en) is a useful commercial benchmark. Its public feature set emphasizes:

- automated toolpath creation and machining process management;
- drilling, roughing, finishing, 3+2, workplane, holder, clamp, pallet, and electrode workflows;
- bulk parameter changes, NC program writing, and setup-sheet generation;
- a guided interface and reusable process knowledge.

The MVP deliberately starts one layer below those vertical wizards: it captures repeated operator behavior into a reviewed recipe. Once reliable shop-floor logs are collected, the most frequent recipes can become dedicated drilling, roughing, finishing, or NC-program modules.

## MVP Decision

An offline learner is the smallest complete slice because it demonstrates capture, normalization, cross-session learning, parameterization, safety review, macro generation, CLI use, a local operator UI, and Codex skill invocation without requiring a licensed PowerMill installation.

The in-process `.NET Framework` add-in is the next delivery step, not a prerequisite for proving the learning pipeline.

