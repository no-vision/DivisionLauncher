using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using Division.Core.Launcher;
using Division.Launcher.Addons;

namespace Division.Launcher;

/// <summary>Platform boundary shared by desktop launch flows. Tickets travel only over stdin.
/// Windows launches natively; Linux retains the verified Proton adapter.</summary>
public sealed class GameLauncher(string Root)
{
    public static string AssetDirectory(string Root) => Directory.Exists(Path.Combine(AppContext.BaseDirectory, "client"))
        ? Path.Combine(AppContext.BaseDirectory, "client") : Path.Combine(Root, "adapters");

    public static void ValidateInstallation(GameInstallation Installation)
    {
        Installation.Display?.Validate();
        if (!OperatingSystem.IsLinux() && !OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("Game launching supports Windows x64 and Linux with Proton.");
        if (!File.Exists(Path.Combine(Installation.GameDirectory, "thedivision.exe")))
            throw new ArgumentException("Choose the game folder containing thedivision.exe.");
        if (OperatingSystem.IsWindows()) return;
        if (!File.Exists(Path.Combine(Installation.ProtonDirectory, "proton")))
            throw new ArgumentException("Choose the Proton 9.0-4 folder containing proton.");
        if (!File.Exists(Path.Combine(Installation.FridaDirectory, "frida-server-17.4.0.exe")))
            throw new ArgumentException("Choose the adapter folder containing frida-server-17.4.0.exe.");
    }

    public static string AdapterFile(string Root) => Path.Combine(AssetDirectory(Root),
        OperatingSystem.IsWindows() ? "windows_client.py" : "proton_client.py");

    public static string AdapterPython(string Root)
    {
        if (!OperatingSystem.IsWindows()) return "python3";
        string Python = Path.Combine(AppContext.BaseDirectory, "python", "python.exe");
        if (!File.Exists(Python)) throw new InvalidOperationException("The bundled Windows adapter runtime is missing. Extract the complete Windows launcher ZIP.");
        return Python;
    }

    public static void AddAdapterArguments(ProcessStartInfo Start, string Root, GameInstallation Installation)
    {
        Start.ArgumentList.Add(AdapterFile(Root));
        Start.ArgumentList.Add("--state-directory"); Start.ArgumentList.Add(LauncherPaths.StateRoot(Root));
        Start.ArgumentList.Add("--game"); Start.ArgumentList.Add(Installation.GameDirectory);
        if (Installation.Display is { } Display)
        {
            Display.Validate();
            Start.ArgumentList.Add("--display-mode"); Start.ArgumentList.Add(Display.Mode);
            Start.ArgumentList.Add("--display-width"); Start.ArgumentList.Add(Display.Width.ToString(System.Globalization.CultureInfo.InvariantCulture));
            Start.ArgumentList.Add("--display-height"); Start.ArgumentList.Add(Display.Height.ToString(System.Globalization.CultureInfo.InvariantCulture));
        }
        if (OperatingSystem.IsLinux())
        {
            Start.ArgumentList.Add("--runtime"); Start.ArgumentList.Add(Installation.ProtonDirectory);
            Start.ArgumentList.Add("--frida"); Start.ArgumentList.Add(Installation.FridaDirectory);
        }
    }

    private static string AdapterHandoff(GameHandoff Handoff)
    {
        JsonSerializerOptions Options = new(JsonSerializerDefaults.Web);
        if (!OperatingSystem.IsWindows()) return JsonSerializer.Serialize(Handoff, Options);
        // Ephemeral client TLS material stays on the adapter's private stdin;
        // never place account tickets or private keys in argv or logs.
        using ECDsa Key = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        CertificateRequest Request = new("CN=private-client", Key, HashAlgorithmName.SHA256);
        using X509Certificate2 Certificate = Request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow.AddDays(2));
        JsonObject Payload = JsonSerializer.SerializeToNode(Handoff, Options)!.AsObject();
        Payload["clientCertificatePem"] = Certificate.ExportCertificatePem();
        Payload["clientPrivateKeyPem"] = Key.ExportECPrivateKeyPem();
        return Payload.ToJsonString(Options);
    }

    public async Task<int> Run(ServerClient Server, GameInstallation Installation, bool EnableAddons, Action<string> Status)
        => await Run(Server, Installation, new AddonSelection(EnableAddons
            ? new AddonCatalog(Root, Path.Combine(LauncherPaths.StateRoot(Root), "profiles/addons.sqlite3")).Discover()
                .Where(Entry => Entry.ScriptPath is not null).Select(Entry => Entry.Id).ToArray() : [], EnableAddons, EnableAddons), Status);

    public async Task<int> Run(ServerClient Server, GameInstallation Installation, AddonSelection Selection, Action<string> Status)
    {
        Status("Checking game build and local launch dependencies…");
        await InstallationPreflight.Check(Root, Installation);
        if (Selection.OverlayEnabled)
        {
            ProcessStartInfo Dependency = new(AdapterPython(Root)) { UseShellExecute = false, RedirectStandardError = true };
            Dependency.ArgumentList.Add("-c");
            Dependency.ArgumentList.Add("from PIL import Image, ImageDraw, ImageFont; ImageFont.load_default(size=14)");
            using Process Check = Process.Start(Dependency) ?? throw new InvalidOperationException("Cannot check overlay dependencies.");
            Task<string> Errors = Check.StandardError.ReadToEndAsync();
            using CancellationTokenSource Timeout = new(TimeSpan.FromSeconds(10));
            try { await Check.WaitForExitAsync(Timeout.Token); }
            catch (OperationCanceledException)
            {
                if (!Check.HasExited) Check.Kill(entireProcessTree: true);
                await Check.WaitForExitAsync();
                throw new InvalidOperationException("The overlay dependency check timed out. Check Python/Pillow or disable the overlays for this launch.");
            }
            await Errors;
            if (Check.ExitCode != 0)
                throw new InvalidOperationException("The in-game overlays require Python Pillow 10.1 or newer. Install the Python Pillow package for your platform, or disable the overlays for this launch.");
        }
        Status("Checking server compatibility and game ports…");
        ServerDescriptor Descriptor = await Server.Verify();
        ServerClient.ValidateCompatibility(Descriptor);
        if (Descriptor.Endpoints is not null)
            ConnectionPreflight.RequireReachable(await ConnectionPreflight.Check(Server.Address, Descriptor.Endpoints));
        // Request the short-lived ticket only after potentially slow installation checks.
        GameHandoff Handoff = await Server.Handoff();
        ConnectionPreflight.RequireReachable(await ConnectionPreflight.Check(Server.Address, Handoff.Endpoints));
        await using PrivateFriendsFeed Friends = new(Server, Root);
        await Friends.Start();
        await using AddonHost? Addons = Selection.Enabled ? new(Server, Root, Selection.Scripts, Selection.PlayerPoseOverlay, Selection.PacketOverlay) : null;
        if (Addons is not null) await Addons.Start();
        ProcessStartInfo Start = new(AdapterPython(Root)) { UseShellExecute = false, RedirectStandardInput = true,
            RedirectStandardOutput = true, RedirectStandardError = true, WorkingDirectory = Root };
        string? DesktopRuntime = Environment.GetEnvironmentVariable("DIVISION_DESKTOP_RUNTIME_DIR") ?? Environment.GetEnvironmentVariable("XDG_RUNTIME_DIR");
        if (!string.IsNullOrWhiteSpace(DesktopRuntime)) Start.Environment["DIVISION_DESKTOP_RUNTIME_DIR"] = DesktopRuntime;
        AddAdapterArguments(Start, Root, Installation);
        Start.ArgumentList.Add("--friends-snapshot"); Start.ArgumentList.Add(Friends.SnapshotPath);
        if (Addons is not null)
        {
            Start.ArgumentList.Add("--addon-events"); Start.ArgumentList.Add(Path.Combine(Addons.Directory, "client-events.txt"));
            if (Selection.OverlayEnabled)
            {
                Start.ArgumentList.Add("--overlay-snapshot"); Start.ArgumentList.Add(Addons.OverlaySnapshot);
            }
        }
        using Process Game = new() { StartInfo = Start };
        Game.OutputDataReceived += (_, Event) =>
        {
            if (Event.Data?.StartsWith("PRIVATE CLIENT:", StringComparison.Ordinal) == true)
                Status("Game is starting. Run folder: " + Event.Data[15..].Trim());
        };
        // Adapter detail stays in its own run log; credentials are never copied to a GUI log.
        Game.ErrorDataReceived += (_, _) => { };
        if (!Game.Start()) throw new InvalidOperationException("Client adapter did not start.");
        Game.BeginOutputReadLine(); Game.BeginErrorReadLine();
        await Game.StandardInput.WriteLineAsync(AdapterHandoff(Handoff));
        Game.StandardInput.Close();
        await Game.WaitForExitAsync();
        return Game.ExitCode;
    }
}
