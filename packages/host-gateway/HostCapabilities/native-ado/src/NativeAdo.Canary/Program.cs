using HostCapabilities.NativeAdo.Canary;

if (args is ["--help"])
{
    Console.WriteLine("Offline only: stdin rendered Helm YAML; stdout patched YAML. No cluster operations.");
    Console.WriteLine("--configmap NAME --deployment NAME --namespace NAME --endpoint DNS:PORT --root-configmap NAME");
    Console.WriteLine("--prepare-live SNAPSHOT_JSON OUTPUT_DIRECTORY (offline; emits guarded deployment patches and a new ConfigMap)");
    return 0;
}

try
{
    if (args is ["--prepare-live", var snapshotPath, var outputDirectory])
    {
        var artifacts = LiveCanaryRenderer.Prepare(await File.ReadAllTextAsync(snapshotPath));
        Directory.CreateDirectory(outputDirectory);
        foreach (var (name, contents) in artifacts)
        {
            await File.WriteAllTextAsync(Path.Combine(outputDirectory, name), contents + Environment.NewLine);
        }
        Console.WriteLine("Prepared offline artifacts only. Provider server CA and transport remain parent-owned prerequisites.");
        return 0;
    }
    if (args.Length != 10)
    {
        throw new ArgumentException();
    }
    var values = new Dictionary<string, string>(StringComparer.Ordinal);
    for (var i = 0; i < args.Length; i += 2)
    {
        values.Add(args[i], args[i + 1]);
    }
    var options = new CanaryPatchOptions(values["--configmap"], values["--deployment"], values["--namespace"],
        values["--endpoint"], values["--root-configmap"]);
    var buffer = new char[8 * 1024 * 1024 + 1];
    var length = await Console.In.ReadBlockAsync(buffer);
    var input = new string(buffer, 0, length);
    var output = RenderedManifestPatcher.Patch(input, options);
    Console.Write(output);
    return 0;
}
catch (Exception exception) when (exception is ArgumentException or InvalidOperationException or
    InvalidCastException or KeyNotFoundException or IOException or System.Text.Json.JsonException or YamlDotNet.Core.YamlException)
{
    Console.Error.WriteLine("Canary render rejected. No output produced; check pinned schema and explicit options.");
    return 1;
}
