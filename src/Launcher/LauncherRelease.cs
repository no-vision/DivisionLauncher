using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Division.Launcher;

public sealed record ReleaseFile(string Path, long Length, string Sha256);
public sealed record ReleaseManifest(string Product, string Version, string Runtime, ReleaseFile[] Files);

/// <summary>Explicit local package import, verified before activation. Hashes detect
/// corruption; they do not authenticate a publisher. The user chooses a trusted
/// package. No community server can nominate executable updates or change this path.
/// Releases contain launcher binaries only; server/game/account files are untouched.</summary>
public sealed class LauncherRelease(string Root)
{
    public static string CurrentVersion => typeof(LauncherRelease).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?
        .InformationalVersion.Split('+')[0] ?? "0.0.0";
    private string LauncherDirectory => LauncherPaths.StateRoot(Root);
    private string ActiveFile => Path.Combine(LauncherDirectory, "active-release.txt");
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public static ReleaseManifest Inspect(string Package)
    {
        using ZipArchive Archive = ZipFile.OpenRead(Package);
        ZipArchiveEntry Entry = Archive.GetEntry("release.json") ?? throw new InvalidDataException("Missing release manifest.");
        if (Entry.Length > 4 * 1024 * 1024) throw new InvalidDataException("Release manifest too large.");
        using Stream Input = Entry.Open();
        ReleaseManifest Manifest = JsonSerializer.Deserialize<ReleaseManifest>(Input, Json) ?? throw new InvalidDataException("Invalid release manifest.");
        if (Manifest.Product != "DivisionPrivateLauncher" || !Version.TryParse(Manifest.Version, out Version? Parsed) || Parsed.Major < 0
            || !Regex.IsMatch(Manifest.Version, @"^\d+\.\d+\.\d+(\.\d+)?$")
            || (Manifest.Runtime != "portable" && Manifest.Runtime != RuntimeInformation.RuntimeIdentifier))
            throw new InvalidDataException("This is not a compatible launcher release.");
        if (Manifest.Files is null || Manifest.Files.Length == 0 || Manifest.Files.Length > 10000)
            throw new InvalidDataException("Invalid release file list.");
        HashSet<string> Paths = new(StringComparer.OrdinalIgnoreCase);
        long Total = 0;
        foreach (ReleaseFile File in Manifest.Files)
        {
            if (string.IsNullOrEmpty(File.Path) || File.Path.Contains('\\') || File.Path.Contains(':') || File.Path.StartsWith('/')
                || File.Path.Split('/').Any(Part => Part is "" or "." or "..") || !Paths.Add(File.Path)
                || File.Length < 0 || File.Length > 512L * 1024 * 1024
                || !Regex.IsMatch(File.Sha256 ?? "", "^[0-9a-fA-F]{64}$"))
                throw new InvalidDataException("Unsafe or duplicate release file.");
            Total = checked(Total + File.Length);
        }
        if (Total > 1024L * 1024 * 1024 || !Paths.Contains("LauncherDesktop.dll")
            || !Paths.Contains("LauncherDesktop.runtimeconfig.json") || !Paths.Contains("LauncherDesktop.deps.json"))
            throw new InvalidDataException("Incomplete or oversized launcher release.");
        return Manifest;
    }

    public string Install(string Package)
    {
        ReleaseManifest Manifest = Inspect(Package);
        string Releases = Path.Combine(LauncherDirectory, "releases"); Directory.CreateDirectory(Releases);
        string Key = Manifest.Version + "-" + Manifest.Runtime;
        string Destination = Path.Combine(Releases, Key);
        if (Directory.Exists(Destination)) throw new IOException("This release is already installed.");
        string Staging = Path.Combine(Releases, ".staging-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(Staging);
        try
        {
            using ZipArchive Archive = ZipFile.OpenRead(Package);
            HashSet<string> Expected = Manifest.Files.Select(File => "app/" + File.Path).Append("release.json").ToHashSet(StringComparer.Ordinal);
            if (Archive.Entries.Count != Expected.Count || Archive.Entries.Any(Entry => !Expected.Remove(Entry.FullName)))
                throw new InvalidDataException("Package entries do not match the manifest.");
            foreach (ReleaseFile File in Manifest.Files)
            {
                ZipArchiveEntry Entry = Archive.GetEntry("app/" + File.Path)!;
                if (Entry.Length != File.Length || ((Entry.ExternalAttributes >> 16) & 0xF000) == 0xA000)
                    throw new InvalidDataException("Release file length or type mismatch.");
                string Target = Path.Combine(Staging, File.Path); Directory.CreateDirectory(Path.GetDirectoryName(Target)!);
                using (Stream Source = Entry.Open())
                using (FileStream Output = new(Target, FileMode.CreateNew)) Source.CopyTo(Output);
                using FileStream Verify = System.IO.File.OpenRead(Target);
                if (!Convert.ToHexString(SHA256.HashData(Verify)).Equals(File.Sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("Release file checksum mismatch: " + File.Path);
                if (!OperatingSystem.IsWindows() && (Entry.ExternalAttributes & (0x49 << 16)) != 0)
                    System.IO.File.SetUnixFileMode(Target, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
            }
            Directory.Move(Staging, Destination);
            string Pending = ActiveFile + ".pending";
            System.IO.File.WriteAllText(Pending, Key + "\n"); System.IO.File.Move(Pending, ActiveFile, true);
            return Key;
        }
        finally { if (Directory.Exists(Staging)) Directory.Delete(Staging, true); }
    }

    public void UseBundled() => System.IO.File.Delete(ActiveFile);
}
