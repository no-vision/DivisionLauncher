using Avalonia;

namespace Division.Launcher.Desktop;

internal static class Program
{
    public static string Root { get; private set; } = "";
    public static string StorePath { get; private set; } = "";

    [STAThread]
    public static void Main(string[] Args)
    {
        string[] Paths = Args.Where(Value => !Value.StartsWith("--", StringComparison.Ordinal)).ToArray();
        Root = Path.GetFullPath(Paths.Length > 0 ? Paths[0] : ".");
        StorePath = Paths.Length > 1 ? Path.GetFullPath(Paths[1]) : Path.Combine(LauncherPaths.StateRoot(Root), "profiles/connections.sqlite3");
        string? Option(string Name) => Args.FirstOrDefault(Value => Value.StartsWith("--" + Name + "=", StringComparison.Ordinal))?[(Name.Length + 3)..];
        if (Option("check-server") is string CheckAddress)
        {
            try
            {
                using ServerClient Probe = new(ServerClient.NormalizeAddress(CheckAddress), Option("certificate"));
                EndpointCheck[] Checks = Probe.CheckConnection().GetAwaiter().GetResult();
                foreach (EndpointCheck Check in Checks)
                    Console.WriteLine($"{Check.Role} {Check.Host}:{Check.Port}: {Check.Detail}");
                ConnectionPreflight.RequireReachable(Checks);
            }
            catch (Exception Error) when (Error is not OutOfMemoryException)
            { Console.Error.WriteLine(Error.Message); Environment.ExitCode = 1; }
            return;
        }
        if (Option("install-update") is string Package)
        {
            Console.WriteLine("Installed launcher release: " + new LauncherRelease(Root).Install(Path.GetFullPath(Package)));
            if (Args.Contains("--prepare-only")) return;
        }
        // Optional operator provisioning stores only a verified server bookmark,
        // never an account, password or login ticket.
        if (Option("add-server") is string Address)
        {
            ConnectionStore Store = new(StorePath);
            string Url = ServerClient.NormalizeAddress(Address);
            ServerConnection? Existing = Store.List().SingleOrDefault(Value => Value.ApiUrl == Url);
            string? Pin = Option("certificate") ?? Existing?.CertificateSha256;
            using ServerClient Client = new(Url, Pin, Existing?.RealmId);
            Division.Core.Launcher.ServerDescriptor Descriptor = Client.Verify().GetAwaiter().GetResult();
            Store.Save(new(Existing?.Id ?? Guid.NewGuid(), Option("label") ?? Descriptor.Name, Url, Descriptor.RealmId, Pin, Existing?.LastUsername));
        }
        if (Args.Contains("--prepare-only")) return;
        string? DesktopRuntime = Environment.GetEnvironmentVariable("DIVISION_DESKTOP_RUNTIME_DIR");
        if (OperatingSystem.IsLinux() && !string.IsNullOrWhiteSpace(DesktopRuntime))
            Environment.SetEnvironmentVariable("XDG_RUNTIME_DIR", DesktopRuntime);
        AppBuilder.Configure<App>().UsePlatformDetect().LogToTrace().StartWithClassicDesktopLifetime([]);
    }
}
