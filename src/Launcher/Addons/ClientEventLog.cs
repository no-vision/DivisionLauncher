namespace Division.Launcher.Addons;

/// <summary>Reads the adapter's append-only diagnostics without excluding its Windows writer.</summary>
public static class ClientEventLog
{
    public static string[]? ReadCompleteLines(string Path)
    {
        try
        {
            using FileStream Stream = new(Path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            // Diagnostics must never exhaust the launcher or disrupt gameplay.
            if (Stream.Length > 4 * 1024 * 1024) return null;
            using StreamReader Reader = new(Stream);
            string Text = Reader.ReadToEnd();
            return Text.Split('\n')[..^1];
        }
        catch (IOException) { return null; } // Absent, briefly locked or being replaced: retry next poll.
        catch (UnauthorizedAccessException) { return null; }
    }
}
