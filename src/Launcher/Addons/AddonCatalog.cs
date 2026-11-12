using Microsoft.Data.Sqlite;

namespace Division.Launcher.Addons;

public sealed record AddonDefinition(string Id, string Name, string Description, string? ScriptPath);
public sealed record AddonSelection(string[] Scripts, bool PlayerPoseOverlay = false, bool PacketOverlay = false)
{
    public bool OverlayEnabled => PlayerPoseOverlay || PacketOverlay;
    public bool Enabled => Scripts.Length > 0 || OverlayEnabled;
}

/// <summary>Discovery reads metadata only; opening the page never executes Lua.
/// Preferences are local, per server, and separate from account credentials.</summary>
public sealed class AddonCatalog(string Root, string SettingsFile)
{
    public AddonDefinition[] Discover()
    {
        string Folder = Path.Combine(LauncherPaths.StateRoot(Root), "addons");
        List<AddonDefinition> Results = [new("player_pose_overlay", "Player position — in-game overlay",
                "One-line reported position and direction. Shift-drag to move; adjustable text and transparency.", null),
            new("packet_overlay", "Packets — in-game overlay",
                "Filter one, several or all message IDs; select a row for plaintext hex. F8 enables overlay controls.", null)];
        if (!Directory.Exists(Folder)) return Results.ToArray();
        foreach (string FileName in Directory.EnumerateFiles(Folder, "*.lua").Order(StringComparer.Ordinal))
        {
            string Id = Path.GetFileNameWithoutExtension(FileName);
            if (Id is "packet_viewer" or "player_pose_overlay" or "packet_overlay") continue;
            string[] Header = File.ReadLines(FileName).Take(12).ToArray();
            string Metadata(string Key, string Fallback) => Header.FirstOrDefault(Line => Line.StartsWith("-- " + Key + ":", StringComparison.Ordinal))?
                .Split(':', 2)[1].Trim() ?? Fallback;
            Results.Add(new(Id, Metadata("Name", Id.Replace('_', ' ')),
                Metadata("Description", "Local Lua observation addon."), FileName));
        }
        return Results.ToArray();
    }

    private SqliteConnection Open()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(SettingsFile))!);
        SqliteConnection Database = new(new SqliteConnectionStringBuilder { DataSource = SettingsFile, Pooling = false }.ToString());
        Database.Open();
        using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = "CREATE TABLE IF NOT EXISTS AddonPreference(ServerId TEXT NOT NULL,AddonId TEXT NOT NULL,Enabled INTEGER NOT NULL CHECK(Enabled IN(0,1)),PRIMARY KEY(ServerId,AddonId))";
        Query.ExecuteNonQuery();
        return Database;
    }

    public bool Enabled(Guid Server, string Id)
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = "SELECT Enabled FROM AddonPreference WHERE ServerId=@Server AND AddonId=@Id";
        Query.Parameters.AddWithValue("Server", Server.ToString("D")); Query.Parameters.AddWithValue("Id", Id);
        return Query.ExecuteScalar() is long Value && Value == 1;
    }

    public void SetEnabled(Guid Server, string Id, bool Enabled)
    {
        if (!Discover().Any(Entry => Entry.Id == Id)) throw new ArgumentException("Unknown addon.");
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = "INSERT INTO AddonPreference VALUES(@Server,@Id,@Enabled) ON CONFLICT(ServerId,AddonId) DO UPDATE SET Enabled=excluded.Enabled";
        Query.Parameters.AddWithValue("Server", Server.ToString("D")); Query.Parameters.AddWithValue("Id", Id);
        Query.Parameters.AddWithValue("Enabled", Enabled ? 1 : 0); Query.ExecuteNonQuery();
    }

    public AddonSelection Selection(Guid Server) => new(Discover().Where(Entry => Entry.ScriptPath is not null && Enabled(Server, Entry.Id)).Select(Entry => Entry.Id).ToArray(),
        Enabled(Server, "player_pose_overlay"), Enabled(Server, "packet_overlay"));
}
