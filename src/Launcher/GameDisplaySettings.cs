using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Division.Launcher;

public sealed record GameDisplaySettings(string Mode = "default", int Width = 1280, int Height = 720)
{
    public void Validate()
    {
        if (Mode is not ("default" or "windowed" or "fullscreen") || Width is < 640 or > 16384 || Height is < 480 or > 16384)
            throw new ArgumentException("Choose a display mode and a resolution between 640 × 480 and 16384 × 16384.");
    }
}

/// <summary>Installation preferences scoped to a launcher profile. Two local
/// players may share game files while using independent private displays.</summary>
public sealed class GameDisplayStore(string ProfilePath)
{
    private string FileName(string GameDirectory)
    {
        string Identity = Path.TrimEndingDirectorySeparator(Path.GetFullPath(GameDirectory));
        if (OperatingSystem.IsWindows()) Identity = Identity.ToUpperInvariant();
        string Hash = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(Identity)));
        return Path.Combine(Path.GetFullPath(ProfilePath) + ".display", Hash + ".json");
    }

    public GameDisplaySettings Read(string GameDirectory)
    {
        string PathName = FileName(GameDirectory);
        if (!File.Exists(PathName)) return new();
        GameDisplaySettings Result = JsonSerializer.Deserialize<GameDisplaySettings>(File.ReadAllText(PathName))
            ?? throw new InvalidDataException("Invalid installation display preferences.");
        Result.Validate(); return Result;
    }

    public void Save(string GameDirectory, GameDisplaySettings Settings)
    {
        Settings.Validate(); string PathName = FileName(GameDirectory);
        Directory.CreateDirectory(Path.GetDirectoryName(PathName)!);
        string Temporary = PathName + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try { File.WriteAllText(Temporary, JsonSerializer.Serialize(Settings)); File.Move(Temporary, PathName, true); }
        finally { if (File.Exists(Temporary)) File.Delete(Temporary); }
    }
}
