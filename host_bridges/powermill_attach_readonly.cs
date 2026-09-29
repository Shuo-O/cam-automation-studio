// Read-only PowerMill attachment using Autodesk's PMAutomation API.
// Compile against the PMAutomation assembly from the target installation.
// This entry point never calls UseExistingInstance/CreateNewInstance or DoCommand.
using System;
using System.Collections.Generic;
using System.IO;
using System.Web.Script.Serialization;
using Autodesk.ProductInterface.PowerMILL;

internal static class PowerMillAttachReadOnly
{
    private static object SelectComObject(int expectedPid)
    {
        List<object> candidates = PMAutomation.GetListOfPmComObjects();
        foreach (object candidate in candidates)
        {
            dynamic raw = candidate;
            int pid = Convert.ToInt32(raw.Debug.ProcessId);
            if (pid == expectedPid)
                return candidate;
        }
        throw new InvalidOperationException("No running PowerMill COM object matched --target-pid.");
    }

    private static Dictionary<string, object> ParseArgs(string[] args)
    {
        var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i + 1 < args.Length; i += 2)
            result[args[i].TrimStart('-')] = args[i + 1];
        return result;
    }

    private static void ConfigureAssemblyResolution(string apiDirectory)
    {
        if (String.IsNullOrWhiteSpace(apiDirectory))
            return;
        AppDomain.CurrentDomain.AssemblyResolve += delegate(object sender, ResolveEventArgs eventArgs)
        {
            string simpleName = new System.Reflection.AssemblyName(eventArgs.Name).Name + ".dll";
            string candidate = Path.Combine(apiDirectory, simpleName);
            return File.Exists(candidate) ? System.Reflection.Assembly.LoadFrom(candidate) : null;
        };
    }

    public static int Main(string[] args)
    {
        try
        {
        var options = ParseArgs(args);
        object apiDirectoryValue;
        if (options.TryGetValue("api-dir", out apiDirectoryValue))
            ConfigureAssemblyResolution(Convert.ToString(apiDirectoryValue));
        int pid = Convert.ToInt32(options["target-pid"]);
        string output = Convert.ToString(options["output"]);
        string instanceId = Convert.ToString(options["instance-id"]);
        string projectId = Convert.ToString(options["project-id"]);
        string material = Convert.ToString(options["material"]);

        object selected = SelectComObject(pid);
        // This overload only wraps the selected COM object; it does not launch PowerMill.
        var automation = new PMAutomation(selected);
        string version = automation.Version.ToString();
        string unitsName = automation.Units.ToString();
        string units;
        if (String.Equals(unitsName, "MM", StringComparison.OrdinalIgnoreCase)
            || unitsName.IndexOf("Millimeter", StringComparison.OrdinalIgnoreCase) >= 0
            || unitsName.IndexOf("Millimetre", StringComparison.OrdinalIgnoreCase) >= 0)
            units = "mm";
        else if (String.Equals(unitsName, "Inches", StringComparison.OrdinalIgnoreCase)
            || unitsName.IndexOf("Inch", StringComparison.OrdinalIgnoreCase) >= 0)
            units = "inch";
        else
            throw new InvalidOperationException("PowerMill Units returned an unknown enum value.");

        var machine = new Dictionary<string, object>
        {
            {"axes", 3}
        };
        var evidence = new Dictionary<string, object>
        {
            {"verified", false},
            {"verification_status", "runtime_observed"},
            {"evidence_type", "official_api_runtime"},
            {"api", "Autodesk.ProductInterface.PowerMILL.PMAutomation"},
            {"instance_id", instanceId},
            {"project_id", projectId},
            {"target_version", version},
            {"process_id", pid},
            {"captured_at", DateTime.UtcNow.ToString("o")},
            {"capture_nonce", Guid.NewGuid().ToString("N")}
        };
        var snapshot = new Dictionary<string, object>
        {
            {"schema_version", 1},
            {"product", "powermill"},
            {"instance_id", instanceId},
            {"project_id", projectId},
            {"target_version", version},
            {"units", units},
            {"material", material},
            {"machine", machine},
            // The reviewed API surface does not provide a side-effect-free,
            // version-stable entity enumeration contract here.
            {"objects", new object[0]},
            {"object_inventory_status", "unavailable"},
            {"geometry_complete", false},
            {"source", "host_read_only"},
            {"captured_in_host", true},
            {"connection_status", "captured"},
            {"live_connection", false},
            {"dry_run", true},
            {"host_evidence", evidence}
        };
        var serializer = new JavaScriptSerializer();
        File.WriteAllText(output, serializer.Serialize(snapshot));
        Console.WriteLine("Read-only PowerMill snapshot written to " + output);
        return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("PowerMill read-only attachment failed: "
                + error.GetType().FullName + ": " + error.Message);
            return 2;
        }
    }
}
