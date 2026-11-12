using Division.Core.Observations;
using Microsoft.Data.Sqlite;

namespace Division.Launcher.Addons;

public sealed record ViewerEvent(long Index, MessageObservation Event);

/// <summary>Typed observation rows; RawMessage is original binary packet evidence, not JSON
/// state. Capture limits stop recording without interrupting the game or live viewer.</summary>
public sealed class ObservationJournal : IDisposable
{
    private readonly SqliteConnection Database;
    private long Bytes;
    private readonly long MaximumBytes;
    public bool Recording { get; private set; } = true;

    public ObservationJournal(string Path, long MaximumBytes = 128 * 1024 * 1024)
    {
        this.MaximumBytes = MaximumBytes;
        Database = new("Data Source=" + Path); Database.Open();
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(Path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        using SqliteCommand Command = Database.CreateCommand();
        Command.CommandText = """
            CREATE TABLE Observation (
                EventIndex INTEGER PRIMARY KEY, SourceSequence INTEGER NOT NULL,
                Time TEXT NOT NULL, ConnectionId TEXT NOT NULL, Source TEXT NOT NULL,
                Kind TEXT NOT NULL, Direction TEXT NOT NULL, Service TEXT NOT NULL,
                MessageId INTEGER, PayloadBytes INTEGER NOT NULL, IdBytes INTEGER NOT NULL,
                RawMessage BLOB NOT NULL, Status TEXT NOT NULL, Detail TEXT NOT NULL,
                Redacted INTEGER NOT NULL, Truncated INTEGER NOT NULL);
            CREATE INDEX ObservationMessage ON Observation(Service, Direction, MessageId);
            """;
        Command.ExecuteNonQuery();
    }

    public void Append(ViewerEvent Entry)
    {
        if (!Recording) return;
        MessageObservation Event = Entry.Event;
        // Reserve conservative row/index overhead as well as bytes; never grow indefinitely.
        Bytes += Event.Message.Length + Event.Detail.Length * 4L + 4096;
        if (Bytes > MaximumBytes) { Recording = false; return; }
        using SqliteCommand Command = Database.CreateCommand();
        Command.CommandText = "INSERT INTO Observation VALUES($Index,$Sequence,$Time,$Connection,$Source,$Kind,$Direction,$Service,$Id,$Payload,$IdBytes,$Raw,$Status,$Detail,$Redacted,$Truncated)";
        (string Key, object Value)[] Values = [
            ("$Index", Entry.Index), ("$Sequence", Event.Sequence), ("$Time", Event.Time.ToString("O")),
            ("$Connection", Event.Connection.ToString("D")), ("$Source", Event.Source), ("$Kind", Event.Kind),
            ("$Direction", Event.Direction), ("$Service", Event.Service), ("$Id", Event.MessageId is uint Id ? (long)Id : DBNull.Value),
            ("$Payload", Event.PayloadBytes), ("$IdBytes", Event.IdBytes), ("$Raw", Event.Message),
            ("$Status", Event.Status), ("$Detail", Event.Detail), ("$Redacted", Event.Redacted), ("$Truncated", Event.Truncated)];
        foreach ((string Key, object Value) in Values) Command.Parameters.AddWithValue(Key, Value);
        Command.ExecuteNonQuery();
    }

    public void Dispose() => Database.Dispose();
}
