namespace HostCapabilities.Tray;

internal enum TrayStatusKind
{
    Ready,
    Starting,
    Unavailable,
    Disabled,
    Stopping,
    Unknown
}

internal sealed record TrayCapabilityDefinition(string Name, string DisplayName, bool Enabled);

internal sealed record TrayCapabilityStatus(TrayCapabilityDefinition Definition, TrayStatusKind Kind);

internal sealed record TraySnapshot(
    TrayStatusKind Overall,
    IReadOnlyList<TrayCapabilityStatus> Capabilities,
    string Summary);

internal static class TrayStatus
{
    internal static IReadOnlyList<TrayCapabilityDefinition> Definitions(HostConfiguration configuration) =>
    [
        new("llm", "Model gateway", configuration.Llm.Enabled),
        new("ado", "Azure DevOps proxy", configuration.Ado.Enabled),
        new("agency", "Agency broker", configuration.Agency.Enabled),
        new("native-ado", "Native ADO", configuration.NativeAdo.Enabled)
    ];

    internal static TraySnapshot Lifecycle(
        TrayStatusKind status,
        IReadOnlyList<TrayCapabilityDefinition> definitions)
    {
        var capabilities = definitions.Select(definition =>
            new TrayCapabilityStatus(definition,
                definition.Enabled ? status : TrayStatusKind.Disabled)).ToArray();
        return new(status, capabilities, Label(status));
    }

    internal static TraySnapshot FromHealth(
        IReadOnlyDictionary<string, CapabilityHealth> health,
        IReadOnlyList<TrayCapabilityDefinition> definitions)
    {
        var capabilities = definitions.Select(definition =>
        {
            if (!definition.Enabled)
                return new TrayCapabilityStatus(definition, TrayStatusKind.Disabled);
            return new TrayCapabilityStatus(definition,
                health.TryGetValue(definition.Name, out var value) ? Map(value.Status) : TrayStatusKind.Unknown);
        }).ToArray();
        var overall = Aggregate(capabilities);
        return new(overall, capabilities, Label(overall));
    }

    internal static Color ColorFor(TrayStatusKind status) => status switch
    {
        TrayStatusKind.Ready => Color.ForestGreen,
        TrayStatusKind.Starting or TrayStatusKind.Unknown => Color.Goldenrod,
        TrayStatusKind.Unavailable => Color.Firebrick,
        TrayStatusKind.Disabled or TrayStatusKind.Stopping => Color.Gray,
        _ => Color.Gray
    };

    internal static string Label(TrayStatusKind status) => status switch
    {
        TrayStatusKind.Ready => "Ready",
        TrayStatusKind.Starting => "Starting",
        TrayStatusKind.Unavailable => "Unavailable",
        TrayStatusKind.Disabled => "Disabled",
        TrayStatusKind.Stopping => "Stopping",
        _ => "Unknown"
    };

    private static TrayStatusKind Aggregate(IReadOnlyList<TrayCapabilityStatus> capabilities)
    {
        var enabled = capabilities.Where(capability => capability.Definition.Enabled).Select(capability => capability.Kind)
            .ToArray();
        if (enabled.Length == 0 || enabled.All(status => status == TrayStatusKind.Ready))
            return TrayStatusKind.Ready;
        if (enabled.Contains(TrayStatusKind.Unavailable))
            return TrayStatusKind.Unavailable;
        if (enabled.Contains(TrayStatusKind.Stopping))
            return TrayStatusKind.Stopping;
        if (enabled.Contains(TrayStatusKind.Starting))
            return TrayStatusKind.Starting;
        if (enabled.Contains(TrayStatusKind.Unknown))
            return TrayStatusKind.Unknown;
        if (enabled.All(status => status is TrayStatusKind.Disabled or TrayStatusKind.Ready))
            return TrayStatusKind.Ready;
        return TrayStatusKind.Unknown;
    }

    private static TrayStatusKind Map(string status) => status switch
    {
        "ready" => TrayStatusKind.Ready,
        "starting" => TrayStatusKind.Starting,
        "unavailable" => TrayStatusKind.Unavailable,
        "disabled" => TrayStatusKind.Disabled,
        "stopping" => TrayStatusKind.Stopping,
        _ => TrayStatusKind.Unknown
    };
}
