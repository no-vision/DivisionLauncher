using Microsoft.Data.Sqlite;

namespace Division.Launcher;

public sealed record ServerConnection(Guid Id, string Label, string ApiUrl, Guid RealmId, string? CertificateSha256, string? LastUsername);
public sealed record GameInstallation(string GameDirectory, string ProtonDirectory, string FridaDirectory,
    GameDisplaySettings? Display = null);
public sealed record SavedAccount(Guid ServerId, Guid AccountId, string Username, string Gamertag);

/// <summary>Local server bookmarks and installation paths. Passwords, launcher sessions and game tickets are never saved.</summary>
public sealed class ConnectionStore
{
    private readonly string Connection;

    public ConnectionStore(string FileName)
    {
        string DirectoryName = Path.GetDirectoryName(Path.GetFullPath(FileName))!;
        Directory.CreateDirectory(DirectoryName);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(DirectoryName, UnixFileMode.UserRead|UnixFileMode.UserWrite|UnixFileMode.UserExecute);
        Connection = new SqliteConnectionStringBuilder { DataSource=FileName, Pooling=false }.ToString();
        using SqliteConnection Database = Open();
        using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = """
            CREATE TABLE IF NOT EXISTS ServerConnection(
              Id TEXT PRIMARY KEY, Label TEXT NOT NULL UNIQUE, ApiUrl TEXT NOT NULL,
              RealmId TEXT NOT NULL, CertificateSha256 TEXT, LastUsername TEXT);
            CREATE TABLE IF NOT EXISTS GameInstallation(
              Singleton INTEGER PRIMARY KEY CHECK(Singleton=1), GameDirectory TEXT NOT NULL,
              ProtonDirectory TEXT NOT NULL, FridaDirectory TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS SavedAccount(
              ServerId TEXT NOT NULL REFERENCES ServerConnection(Id) ON DELETE CASCADE,
              AccountId TEXT NOT NULL, Username TEXT NOT NULL, Gamertag TEXT NOT NULL,
              PRIMARY KEY(ServerId,AccountId));
            """;
        Query.ExecuteNonQuery();
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(FileName, UnixFileMode.UserRead|UnixFileMode.UserWrite);
    }

    private SqliteConnection Open()
    {
        SqliteConnection Database = new(Connection); Database.Open();
        using SqliteCommand Command = Database.CreateCommand();
        Command.CommandText = "PRAGMA foreign_keys=ON"; Command.ExecuteNonQuery();
        return Database;
    }

    #region Saved Account Identities

    public SavedAccount[] Accounts(Guid ServerId)
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = "SELECT AccountId,Username,Gamertag FROM SavedAccount WHERE ServerId=@Server ORDER BY Username";
        Query.Parameters.AddWithValue("Server", ServerId.ToString("D"));
        using SqliteDataReader Rows = Query.ExecuteReader(); List<SavedAccount> Results = [];
        while (Rows.Read()) Results.Add(new(ServerId, Guid.Parse(Rows.GetString(0)), Rows.GetString(1), Rows.GetString(2)));
        return Results.ToArray();
    }

    public void Remember(Guid ServerId, Division.Core.Launcher.AccountProfile Profile)
    {
        ServerConnection Server = List().Single(Value => Value.Id == ServerId);
        if (Server.RealmId != Profile.RealmId) throw new ArgumentException("Account belongs to a different server.");
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = """
            INSERT INTO SavedAccount VALUES(@Server,@Account,@Username,@Gamertag)
            ON CONFLICT(ServerId,AccountId) DO UPDATE SET Username=excluded.Username,Gamertag=excluded.Gamertag
            """;
        Query.Parameters.AddWithValue("Server", ServerId.ToString("D"));
        Query.Parameters.AddWithValue("Account", Profile.AccountId.ToString("D"));
        Query.Parameters.AddWithValue("Username", Profile.Username); Query.Parameters.AddWithValue("Gamertag", Profile.Gamertag);
        Query.ExecuteNonQuery();
    }

    public void Forget(Guid ServerId, Guid AccountId)
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = "DELETE FROM SavedAccount WHERE ServerId=@Server AND AccountId=@Account";
        Query.Parameters.AddWithValue("Server", ServerId.ToString("D")); Query.Parameters.AddWithValue("Account", AccountId.ToString("D"));
        Query.ExecuteNonQuery();
    }

    #endregion

    public ServerConnection[] List()
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = "SELECT Id,Label,ApiUrl,RealmId,CertificateSha256,LastUsername FROM ServerConnection ORDER BY Label";
        using SqliteDataReader Rows = Query.ExecuteReader(); List<ServerConnection> Servers = [];
        while (Rows.Read()) Servers.Add(new(Guid.Parse(Rows.GetString(0)), Rows.GetString(1), Rows.GetString(2), Guid.Parse(Rows.GetString(3)),
            Rows.IsDBNull(4)?null:Rows.GetString(4), Rows.IsDBNull(5)?null:Rows.GetString(5)));
        return Servers.ToArray();
    }

    public void Save(ServerConnection Server)
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText = """
            INSERT INTO ServerConnection VALUES(@Id,@Label,@Url,@Realm,@Pin,@Username)
            ON CONFLICT(Id) DO UPDATE SET Label=excluded.Label,ApiUrl=excluded.ApiUrl,RealmId=excluded.RealmId,
              CertificateSha256=excluded.CertificateSha256,LastUsername=excluded.LastUsername
            """;
        Query.Parameters.AddWithValue("Id",Server.Id.ToString("D")); Query.Parameters.AddWithValue("Label",Server.Label);
        Query.Parameters.AddWithValue("Url",Server.ApiUrl); Query.Parameters.AddWithValue("Realm",Server.RealmId.ToString("D"));
        Query.Parameters.AddWithValue("Pin",(object?)Server.CertificateSha256??DBNull.Value);
        Query.Parameters.AddWithValue("Username",(object?)Server.LastUsername??DBNull.Value); Query.ExecuteNonQuery();
    }

    public void Remove(Guid Id)
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText="DELETE FROM ServerConnection WHERE Id=@Id"; Query.Parameters.AddWithValue("Id",Id.ToString("D")); Query.ExecuteNonQuery();
    }

    public GameInstallation? Installation()
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText="SELECT GameDirectory,ProtonDirectory,FridaDirectory FROM GameInstallation WHERE Singleton=1";
        using SqliteDataReader Row = Query.ExecuteReader(); return Row.Read()?new(Row.GetString(0),Row.GetString(1),Row.GetString(2)):null;
    }

    public void SetInstallation(GameInstallation Installation)
    {
        using SqliteConnection Database = Open(); using SqliteCommand Query = Database.CreateCommand();
        Query.CommandText="INSERT INTO GameInstallation VALUES(1,@Game,@Proton,@Frida) ON CONFLICT(Singleton) DO UPDATE SET GameDirectory=@Game,ProtonDirectory=@Proton,FridaDirectory=@Frida";
        Query.Parameters.AddWithValue("Game",Installation.GameDirectory); Query.Parameters.AddWithValue("Proton",Installation.ProtonDirectory);
        Query.Parameters.AddWithValue("Frida",Installation.FridaDirectory); Query.ExecuteNonQuery();
    }
}
