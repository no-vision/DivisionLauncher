using System.Text.Json;
using Division.Core.Launcher;

namespace Division.Launcher;

/// <summary>Credential-free, expiring private roster for either game adapter.
/// Tokens remain inside ServerClient; updates replace the file atomically.</summary>
public sealed class PrivateFriendsFeed : IAsyncDisposable
{
    private readonly ServerClient Server;
    private readonly CancellationTokenSource Stop = new();
    private readonly string DirectoryPath;
    private Task? Polling;
    public string SnapshotPath { get; }

    public PrivateFriendsFeed(ServerClient Server, string Root)
    {
        this.Server = Server;
        DirectoryPath = Path.Combine(LauncherPaths.StateRoot(Root), "runtime/friends", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(DirectoryPath);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(DirectoryPath,
            UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        SnapshotPath = Path.Combine(DirectoryPath, "roster.json");
    }

    public async Task Start()
    {
        await Fetch();
        Polling = Poll();
    }

    private async Task Poll()
    {
        try
        {
            while (!Stop.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(3), Stop.Token);
                try { await Fetch(); }
                catch (IOException) { /* The last published roster expires; retry on the next interval. */ }
                catch (UnauthorizedAccessException) { /* Do not retain an unbounded stale roster. */ }
            }
        }
        catch (OperationCanceledException) when (Stop.IsCancellationRequested) { }
    }

    private async Task Fetch()
    {
        List<PrivateFriend> Friends = [];
        try
        {
            string? After = null;
            HashSet<string> Cursors = [];
            // Bound both output and HTTP work, including malformed pagination.
            for (int PageNumber = 0; PageNumber < 8 && Friends.Count < 256; PageNumber++)
            {
                PrivateFriendsPage Page = await Server.Friends(After).WaitAsync(Stop.Token);
                Friends.AddRange(Page.Entries.Where(Entry => Entry.State == PrivateFriendState.Friends)
                    .Take(256 - Friends.Count));
                After = Page.Next;
                if (After is null || !Cursors.Add(After)) break;
            }
        }
        catch (Exception Error) when (Error is HttpRequestException or InvalidOperationException or JsonException or TaskCanceledException)
        { Friends.Clear(); }
        if (Stop.IsCancellationRequested) return;
        string Json = JsonSerializer.Serialize(new
        {
            expires = DateTimeOffset.UtcNow.AddSeconds(10).ToUnixTimeSeconds(),
            entries = Friends.DistinctBy(Friend => Friend.AccountId).Select(Friend => new
            { accountId = Friend.AccountId, name = Friend.Username, online = Friend.Online }).ToArray()
        });
        string Temporary = SnapshotPath + ".tmp";
        await File.WriteAllTextAsync(Temporary, Json, Stop.Token);
        File.Move(Temporary, SnapshotPath, overwrite: true);
    }

    public async ValueTask DisposeAsync()
    {
        Stop.Cancel();
        if (Polling is not null)
            try { await Polling; } catch (OperationCanceledException) { }
        Stop.Dispose();
        Directory.Delete(DirectoryPath, recursive: true);
    }
}
