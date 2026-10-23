using System.Text.Json;
using AdminShellNS;
using Aas = AasCore.Aas3_1;

// Usage: AasxCompat <corpus-dir> <tool-label>
var corpus = Path.GetFullPath(args[0]);
var tool = args[1];
var rows = new List<Dictionary<string, object?>>();
var files = Directory.EnumerateFiles(corpus, "*", SearchOption.AllDirectories)
    .Where(f => f.EndsWith(".json") || f.EndsWith(".xml") || f.EndsWith(".aasx"))
    .Where(f => Path.GetFileName(f) != "probes-manifest.json")
    .OrderBy(f => f, StringComparer.Ordinal);
foreach (var file in files)
{
    var row = new Dictionary<string, object?>
    {
        ["file"] = Path.GetRelativePath(corpus, file),
        ["payload"] = "(file)",
        ["tool"] = tool,
    };
    try
    {
        using var package = new AdminShellPackageEnv(file);
        var env = package.AasEnv;
        var objects = (env?.AssetAdministrationShells?.Count ?? 0) + (env?.Submodels?.Count ?? 0)
                      + (env?.ConceptDescriptions?.Count ?? 0);
        var errors = env == null ? new List<Aas.Reporting.Error>() : Aas.Verification.Verify(env).ToList();
        row["status"] = "pass";
        row["objects"] = objects;
        row["verification_errors"] = errors.Count;
        row["detail"] = errors.Count == 0 ? "" :
            $"{errors.Count} verification errors; first: {Aas.Reporting.GenerateJsonPath(errors[0].PathSegments)}: {errors[0].Cause}";
    }
    catch (Exception error)
    {
        var root = error;
        while (root.InnerException != null) root = root.InnerException;
        row["status"] = "fail";
        row["detail"] = $"{error.GetType().Name}: {error.Message}" + (root != error ? $" | root {root.GetType().Name}: {root.Message}" : "");
    }
    var detail = ((string?)row["detail"] ?? "").Replace(corpus, "<corpus>");
    row["detail"] = detail.Length > 600 ? detail[..600] : detail;
    rows.Add(row);
}
Console.WriteLine(JsonSerializer.Serialize(rows, new JsonSerializerOptions { WriteIndented = true }));
