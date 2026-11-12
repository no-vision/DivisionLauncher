using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
using Division.Launcher.Addons;

namespace Division.Launcher.Desktop.Tests;

internal static class LauncherLocalChecks
{
    public static void Run(string Root)
    {
        string TestRoot = Path.Combine(Root, ".cache/tests", "local-" + Guid.NewGuid().ToString("N"));
        string Addons = Path.Combine(TestRoot, "addons"); Directory.CreateDirectory(Addons);
        File.WriteAllText(Path.Combine(Addons, "one.lua"), "-- Name: Example\n-- Description: Test observer\nerror('must not execute during discovery')");
        AddonCatalog Catalog = new(TestRoot, Path.Combine(TestRoot, "preferences.sqlite3"));
        int Checks = 0;
        void Check(bool Value, string Message) { if (!Value) throw new InvalidOperationException(Message); Checks++; }
        string LegacyRoot = Path.Combine(TestRoot, "existing-installation");
        string LegacyState = Path.Combine(LegacyRoot, "Production", "launcher");
        string LegacyProfile = Path.Combine(LegacyState, "profiles", "connections.sqlite3");
        ConnectionStore LegacyStore = new(LegacyProfile);
        Guid Bookmark = Guid.NewGuid();
        LegacyStore.Save(new(Bookmark, "Existing bookmark", "https://example.invalid", Guid.NewGuid(), null, null));
        Check(LauncherPaths.StateRoot(LegacyRoot) == LegacyState, "update retains existing profile location");
        Check(new ConnectionStore(Path.Combine(LauncherPaths.StateRoot(LegacyRoot), "profiles", "connections.sqlite3"))
            .List().Single().Id == Bookmark, "existing bookmark remains accessible after layout update");
        Check(LauncherPaths.StateRoot(TestRoot) == TestRoot, "new installation stores state at bundle root");
        Directory.CreateDirectory(Path.Combine(LegacyRoot, "profiles"));
        Check(LauncherPaths.StateRoot(LegacyRoot) == LegacyRoot && File.Exists(LegacyProfile),
            "explicit new profiles take precedence without deleting legacy profiles");
        GameDisplayStore DisplayA = new(Path.Combine(TestRoot, "one.sqlite3"));
        GameDisplayStore DisplayB = new(Path.Combine(TestRoot, "two.sqlite3"));
        string GamePath = Path.Combine(TestRoot, "game");
        DisplayA.Save(GamePath, new("windowed", 1280, 720));
        DisplayB.Save(GamePath, new("fullscreen", 1920, 1080));
        Check(new GameDisplayStore(Path.Combine(TestRoot, "one.sqlite3")).Read(GamePath) == new GameDisplaySettings("windowed", 1280, 720), "display survives reopening profile");
        Check(DisplayB.Read(GamePath) == new GameDisplaySettings("fullscreen", 1920, 1080), "same installation has independent player display preferences");
        Check(DisplayA.Read(Path.Combine(TestRoot, "other-game")).Mode == "default", "display isolated per installation");
        System.Diagnostics.ProcessStartInfo Launch = new("python3");
        GameLauncher.AddAdapterArguments(Launch, Root, new(GamePath, "proton", "adapter", DisplayA.Read(GamePath)));
        Check(Launch.ArgumentList.Contains("--display-mode") && Launch.ArgumentList.Contains("windowed") && Launch.ArgumentList.Contains("1280"), "display reaches platform adapter");
        Guid ServerA = Guid.NewGuid(), ServerB = Guid.NewGuid();
        Check(Catalog.Discover().Single(Entry => Entry.Id == "one").Name == "Example", "metadata discovery without execution");
        Check(!Catalog.Selection(ServerA).Enabled, "addons opt in");
        Catalog.SetEnabled(ServerA, "one", true); Catalog.SetEnabled(ServerA, "packet_overlay", true);
        Check(Catalog.Selection(ServerA).PacketOverlay && Catalog.Selection(ServerA).Scripts.SequenceEqual(new[] { "one" }), "internal overlay and scripts independently selected");
        Check(!Catalog.Discover().Any(Entry => Entry.Id == "packet_viewer"), "retired browser addon is not offered");
        Check(!Catalog.Selection(ServerB).Enabled, "addon preferences isolated by server");
        File.Delete(Path.Combine(Addons, "one.lua"));
        Check(Catalog.Selection(ServerA).Scripts.Length == 0, "removed addon cannot load through stale preference");

        string EventsPath = Path.Combine(TestRoot, "client-events.txt");
        using (FileStream Writer = new(EventsPath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite))
        {
            Writer.Write(System.Text.Encoding.UTF8.GetBytes("1.0 game_attached\n2.0 partial")); Writer.Flush();
            Check(ClientEventLog.ReadCompleteLines(EventsPath) is ["1.0 game_attached"], "adapter writer remains open while complete lifecycle records are read");
        }
        using (FileStream Exclusive = new(EventsPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            Check(ClientEventLog.ReadCompleteLines(EventsPath) is null, "brief file contention is retried without failing the addon host");

        string Package(string Name, string Version, bool Corrupt = false, string? Extra = null)
        {
            string PathName = Path.Combine(TestRoot, Name + ".zip");
            List<ReleaseFile> Files = [];
            using ZipArchive Archive = ZipFile.Open(PathName, ZipArchiveMode.Create);
            foreach (string FileName in new[] { "LauncherDesktop.dll", "LauncherDesktop.deps.json", "LauncherDesktop.runtimeconfig.json" })
            {
                byte[] Content = System.Text.Encoding.UTF8.GetBytes("test " + FileName);
                Files.Add(new(FileName, Content.Length, Convert.ToHexString(SHA256.HashData(Content))));
                using Stream Output = Archive.CreateEntry("app/" + FileName).Open();
                Output.Write(Corrupt ? new byte[Content.Length] : Content);
            }
            if (Extra is not null) { using Stream Output = Archive.CreateEntry(Extra).Open(); Output.WriteByte(1); }
            using Stream Manifest = Archive.CreateEntry("release.json").Open();
            JsonSerializer.Serialize(Manifest, new ReleaseManifest("DivisionPrivateLauncher", Version, "portable", Files.ToArray()), new JsonSerializerOptions(JsonSerializerDefaults.Web));
            return PathName;
        }
        LauncherRelease Releases = new(TestRoot);
        string Active = Path.Combine(TestRoot, "active-release.txt");
        Releases.Install(Package("good", "0.2.1"));
        Check(File.ReadAllText(Active).Trim() == "0.2.1-portable", "verified release activates for next start");
        foreach ((string Name, bool Corrupt, string? Extra) Failure in new[]
            { ("corrupt", true, (string?)null), ("escape", false, "app/../../escape"), ("duplicate", false, "app/LauncherDesktop.dll") })
        {
            bool Rejected = false;
            try { Releases.Install(Package(Failure.Name, "0.2.2", Failure.Corrupt, Failure.Extra)); }
            catch (InvalidDataException) { Rejected = true; }
            Check(Rejected && File.ReadAllText(Active).Trim() == "0.2.1-portable", "invalid update leaves active release intact: " + Failure.Name);
        }
        Check(!Directory.EnumerateDirectories(Path.Combine(TestRoot, "releases"), ".staging-*").Any(), "failed update staging removed");
        Releases.UseBundled();
        Check(!File.Exists(Active) && Directory.Exists(Path.Combine(TestRoot, "releases/0.2.1-portable")), "rollback preserves installed release");
        Console.WriteLine($"PASS: {Checks} launcher addon/update checks. Artifacts: {TestRoot}");
    }
}
