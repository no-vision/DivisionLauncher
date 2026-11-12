using Division.Core.Observations;

namespace Division.Launcher.Addons;

/// <summary>Launcher-owned observation journal, Lua host and in-game overlay feed.
/// Account tokens stay in ServerClient. No companion browser listener is opened.</summary>
public sealed class AddonHost : IAsyncDisposable
{
    private readonly ServerClient Server;
    private readonly string Root;
    private readonly HashSet<string>? EnabledScripts;
    private readonly object Gate = new();
    private readonly Queue<ViewerEvent> Events = new();
    private readonly List<LuaAddon> Addons = [];
    private readonly List<string> LoadErrors = [];
    private readonly CancellationTokenSource Stop = new();
    private readonly ObservationJournal Journal;
    private Task? Polling;
    private long Index;
    private long Evicted;
    private long PacketsObserved;
    private Guid? Stream;
    private long Cursor;
    private int ClientLines;
    private string Status = "Connecting";
    private string? CaptureError;
    private readonly bool PoseOverlay;
    private readonly bool PacketOverlay;
    private PlayerPoseObservation? Pose;
    private Guid? PoseConnection;
    public string OverlaySnapshot => Path.Combine(Directory, "overlay.txt");
    public string Directory { get; }

    public AddonHost(ServerClient Server, string Root, string[]? EnabledScripts = null, bool PoseOverlay = false, bool PacketOverlay = false)
    {
        this.Server = Server; this.Root = Root;
        this.EnabledScripts = EnabledScripts?.ToHashSet(StringComparer.Ordinal);
        this.PoseOverlay = PoseOverlay;
        this.PacketOverlay = PacketOverlay;
        Directory = Path.Combine(LauncherPaths.StateRoot(Root), "captures", Guid.NewGuid().ToString("N"));
        System.IO.Directory.CreateDirectory(Directory);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(Directory, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        Journal = new(Path.Combine(Directory, "observations.sqlite3"));
        Reload();
    }

    #region Startup

    public async Task Start()
    {
        Console.WriteLine("Observation capture: " + Path.Combine(Directory, "observations.sqlite3"));
        // Subscribe before the game starts so its first messages can be recorded.
        await Fetch();
        Polling = Poll();
    }

    #endregion

    #region Observation and Addon Dispatch

    private void Reload()
    {
        foreach (LuaAddon Addon in Addons) Addon.Dispose();
        Addons.Clear(); LoadErrors.Clear();
        string Folder = Path.Combine(LauncherPaths.StateRoot(Root), "addons");
        if (!System.IO.Directory.Exists(Folder)) return;
        foreach (string FileName in System.IO.Directory.EnumerateFiles(Folder, "*.lua").Order())
        {
            if (EnabledScripts is not null && !EnabledScripts.Contains(Path.GetFileNameWithoutExtension(FileName))) continue;
            try { Addons.Add(new(Path.GetFileNameWithoutExtension(FileName), File.ReadAllText(FileName))); }
            catch (Exception Error) when (Error is NLua.Exceptions.LuaException or IOException or InvalidCastException)
            { LoadErrors.Add(Path.GetFileName(FileName) + ": " + Error.Message[..Math.Min(Error.Message.Length, 200)]); }
        }
    }

    private async Task Poll()
    {
        try
        {
            while (!Stop.IsCancellationRequested)
            // The public observation endpoint allows120 requests/minute. Leave
            // headroom for startup/retries; UI input still runs independently20Hz.
            { await Task.Delay(PoseOverlay || PacketOverlay ? 600 : 1000, Stop.Token); await Fetch(); }
        }
        catch (OperationCanceledException) when (Stop.IsCancellationRequested) { }
    }

    private async Task Fetch()
    {
        string ClientEvents = Path.Combine(Directory, "client-events.txt");
        if (ClientEventLog.ReadCompleteLines(ClientEvents) is string[] Lines)
        {
            lock (Gate)
            {
                foreach (string Line in Lines.Skip(ClientLines))
                {
                    string[] Parts = Line.Split(' ', 2);
                    if (Parts.Length != 2 || !System.Text.RegularExpressions.Regex.IsMatch(Parts[1], "^[a-z_]+$")) continue;
                    DateTimeOffset Time = double.TryParse(Parts[0], System.Globalization.NumberStyles.Float,
                        System.Globalization.CultureInfo.InvariantCulture, out double Seconds) && double.IsFinite(Seconds)
                        ? DateTimeOffset.FromUnixTimeMilliseconds((long)(Seconds * 1000)) : DateTimeOffset.UtcNow;
                    Add(Local("client_hook", Parts[1], "Fixed lifecycle event from the existing private client adapter.") with { Source = "client", Time = Time });
                }
                ClientLines = Lines.Length;
            }
        }
        try
        {
            ObservationBatch Batch = await Server.Observations(new(Stream, Cursor));
            lock (Gate)
            {
                if (Batch.Reset && Stream is not null) Add(Local("diagnostic", "stream_reset", "Server observation stream restarted; history is separated by this marker."));
                if (Batch.Reset) { Pose = null; PoseConnection = null; }
                Stream = Batch.Stream; Cursor = Batch.Cursor; Evicted = Batch.Evicted;
                foreach (MessageObservation Event in Batch.Events) Add(Event);
                Status = "Connected — server plaintext observations; S2C queued is not client acknowledgement";
            }
        }
        catch (Exception Error) when (Error is HttpRequestException or InvalidOperationException or TaskCanceledException or System.Text.Json.JsonException)
        {
            lock (Gate) Status = "Feed unavailable. Check server --developer-diagnostics and launcher login. " + Error.GetType().Name;
        }
        if (PoseOverlay || PacketOverlay)
        {
            // One bounded snapshot per poll, never filesystem work for each packet.
            // Atomic replacement prevents the private adapter seeing half a line.
            try
            {
                string Text;
                lock (Gate)
                {
                    // Bound both count and retained bytes. Full originals remain in
                    // the observation journal; redacted payloads never reach either UI.
                    int Bytes = 0;
                    ViewerEvent[] Packets = PacketOverlay ? Events.Reverse().Where(Entry => Entry.Event.Kind == "packet")
                        .Take(128).TakeWhile(Entry => (Bytes += Entry.Event.Message.Length + 1024) <= 512 * 1024)
                        .Reverse().Select(Entry => Entry.Event.Redacted
                            ? Entry with { Event = Entry.Event with { Message = [] } } : Entry).ToArray() : [];
                    Text = System.Text.Json.JsonSerializer.Serialize(new
                    {
                        Version = 2, PoseEnabled = PoseOverlay, PacketsEnabled = PacketOverlay,
                        Pose = Pose?.CompactText(DateTimeOffset.UtcNow) ?? "POS / WAITING FOR C2S15",
                        Status = CaptureError is null ? Status : Status + " | " + CaptureError,
                        Cursor = Index, Evicted, PacketsObserved, Events = Packets
                    }, new System.Text.Json.JsonSerializerOptions(System.Text.Json.JsonSerializerDefaults.Web));
                }
                File.WriteAllText(OverlaySnapshot + ".tmp", Text);
                File.Move(OverlaySnapshot + ".tmp", OverlaySnapshot, true);
            }
            catch (IOException) { /* The renderer marks a stopped producer stale. */ }
        }
    }

    private static MessageObservation Local(string Kind, string Status, string Detail)
        => new(0, DateTimeOffset.UtcNow, Guid.Empty, "launcher", Kind, "", "", null, 0, 0, [], Status, Detail);

    private void Add(MessageObservation Event)
    {
        if (PoseOverlay)
        {
            if (Event.Service == "game_server" && Event.Direction == "C2S" && Event.MessageId == 0)
            { Pose = null; PoseConnection = Event.Connection; }
            PlayerPoseObservation? Sample = PlayerPoseObservation.Read(Event);
            if (Sample is not null && (PoseConnection is null || PoseConnection == Sample.Connection) &&
                (Pose is null || Sample.Time >= Pose.Time))
            { Pose = Sample; PoseConnection = Sample.Connection; }
        }
        Store(Event);
        foreach (LuaAddon Addon in Addons)
        {
            string? Annotation = Addon.Observe(Event);
            if (Annotation is not null) Store(Local("annotation", Addon.Name, Annotation));
        }
    }

    private void Store(MessageObservation Event)
    {
        ViewerEvent Entry = new(++Index, Event);
        if (Event.Kind == "packet") PacketsObserved++;
        Events.Enqueue(Entry); while (Events.Count > 2000) Events.Dequeue();
        try { if (CaptureError is null) Journal.Append(Entry); }
        catch (Exception Error) when (Error is Microsoft.Data.Sqlite.SqliteException or IOException)
        { CaptureError = "Capture write failed: " + Error.GetType().Name; }
    }

    #endregion

    public async ValueTask DisposeAsync()
    {
        Stop.Cancel(); if (Polling is not null) await Polling;
        try { await Server.Observations(new(Stream, Cursor, false)); }
        catch (Exception Error) when (Error is HttpRequestException or InvalidOperationException or TaskCanceledException) { }
        lock (Gate) { foreach (LuaAddon Addon in Addons) Addon.Dispose(); Journal.Dispose(); }
        Stop.Dispose();
    }
}
