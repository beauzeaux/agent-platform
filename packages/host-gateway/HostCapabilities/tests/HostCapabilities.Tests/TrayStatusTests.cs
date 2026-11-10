using HostCapabilities.Tray;

namespace HostCapabilities.Tests;

[TestClass]
public sealed class TrayStatusTests
{
    [TestMethod]
    public void DefinitionsPreserveCapabilityOrderAndEnabledState()
    {
        var configuration = new HostConfiguration
        {
            Llm = new() { Enabled = false },
            Ado = new() { Enabled = true },
            Agency = new() { Enabled = false },
            NativeAdo = new() { Enabled = true }
        };

        var definitions = TrayStatus.Definitions(configuration);

        CollectionAssert.AreEqual(
            new[] { "llm", "ado", "agency", "native-ado" },
            definitions.Select(definition => definition.Name).ToArray());
        CollectionAssert.AreEqual(
            new[] { "Model gateway", "Azure DevOps proxy", "Agency broker", "Native ADO" },
            definitions.Select(definition => definition.DisplayName).ToArray());
        CollectionAssert.AreEqual(
            new[] { false, true, false, true },
            definitions.Select(definition => definition.Enabled).ToArray());
    }

    [TestMethod]
    [DataRow("ready", "Ready", "Ready")]
    [DataRow("starting", "Starting", "Starting")]
    [DataRow("unavailable", "Unavailable", "Unavailable")]
    [DataRow("disabled", "Ready", "Ready")]
    [DataRow("stopping", "Stopping", "Stopping")]
    [DataRow("unexpected", "Unknown", "Unknown")]
    public void FromHealthMapsCapabilityStatusAndAggregate(
        string reportedStatus,
        string expectedOverall,
        string expectedSummary)
    {
        var definition = new TrayCapabilityDefinition("llm", "Model gateway", true);

        var snapshot = TrayStatus.FromHealth(
            new Dictionary<string, CapabilityHealth>
            {
                ["llm"] = new(reportedStatus, "test")
            },
            [definition]);

        Assert.AreEqual(Enum.Parse<TrayStatusKind>(expectedOverall), snapshot.Overall);
        Assert.AreEqual(expectedSummary, snapshot.Summary);
        Assert.AreEqual(
            reportedStatus switch
            {
                "ready" => TrayStatusKind.Ready,
                "starting" => TrayStatusKind.Starting,
                "unavailable" => TrayStatusKind.Unavailable,
                "disabled" => TrayStatusKind.Disabled,
                "stopping" => TrayStatusKind.Stopping,
                _ => TrayStatusKind.Unknown
            },
            snapshot.Capabilities.Single().Kind);
    }

    [TestMethod]
    public void FromHealthUsesFailurePrecedenceAcrossEnabledCapabilities()
    {
        var definitions = Definitions(allEnabled: true);

        AssertAggregate(TrayStatusKind.Unavailable, definitions, "unavailable", "starting", "unknown", "stopping");
        AssertAggregate(TrayStatusKind.Stopping, definitions, "stopping", "starting", "unknown", "ready");
        AssertAggregate(TrayStatusKind.Starting, definitions, "starting", "unknown", "ready", "ready");
        AssertAggregate(TrayStatusKind.Unknown, definitions, "unknown", "ready", "ready", "ready");
        AssertAggregate(TrayStatusKind.Ready, definitions, "ready", "ready", "ready", "ready");
    }

    [TestMethod]
    public void FromHealthKeepsConfiguredDisabledCapabilitiesDisabledAndExcludesThemFromAggregate()
    {
        var definitions = Definitions(allEnabled: false);
        var snapshot = TrayStatus.FromHealth(
            new Dictionary<string, CapabilityHealth>
            {
                ["llm"] = new("unavailable", "test"),
                ["ado"] = new("unavailable", "test"),
                ["agency"] = new("ready", "test"),
                ["native-ado"] = new("ready", "test")
            },
            definitions);

        CollectionAssert.AreEqual(
            new[]
            {
                TrayStatusKind.Disabled,
                TrayStatusKind.Disabled,
                TrayStatusKind.Ready,
                TrayStatusKind.Ready
            },
            snapshot.Capabilities.Select(capability => capability.Kind).ToArray());
        Assert.AreEqual(TrayStatusKind.Ready, snapshot.Overall);
    }

    [TestMethod]
    public void LifecycleSnapshotMarksOnlyEnabledCapabilitiesWithLifecycleStatus()
    {
        var snapshot = TrayStatus.Lifecycle(TrayStatusKind.Starting, Definitions(allEnabled: false));

        CollectionAssert.AreEqual(
            new[]
            {
                TrayStatusKind.Disabled,
                TrayStatusKind.Disabled,
                TrayStatusKind.Starting,
                TrayStatusKind.Starting
            },
            snapshot.Capabilities.Select(capability => capability.Kind).ToArray());
        Assert.AreEqual(TrayStatusKind.Starting, snapshot.Overall);
        Assert.AreEqual("Starting", snapshot.Summary);
    }

    [TestMethod]
    public void FromHealthTreatsMissingHealthAsUnknown()
    {
        var snapshot = TrayStatus.FromHealth(new Dictionary<string, CapabilityHealth>(),
            [new TrayCapabilityDefinition("llm", "Model gateway", true)]);

        Assert.AreEqual(TrayStatusKind.Unknown, snapshot.Capabilities.Single().Kind);
        Assert.AreEqual(TrayStatusKind.Unknown, snapshot.Overall);
    }

    private static IReadOnlyList<TrayCapabilityDefinition> Definitions(bool allEnabled) =>
    [
        new("llm", "Model gateway", allEnabled),
        new("ado", "Azure DevOps proxy", allEnabled),
        new("agency", "Agency broker", true),
        new("native-ado", "Native ADO", true)
    ];

    private static void AssertAggregate(
        TrayStatusKind expected,
        IReadOnlyList<TrayCapabilityDefinition> definitions,
        params string[] statuses)
    {
        var health = definitions.Zip(statuses, (definition, status) =>
                new KeyValuePair<string, CapabilityHealth>(definition.Name, new(status, "test")))
            .ToDictionary();

        Assert.AreEqual(expected, TrayStatus.FromHealth(health, definitions).Overall,
            $"Statuses: {string.Join(", ", statuses)}");
    }
}
