namespace Division.Launcher;

public static class LauncherPaths
{
    private static bool HasState(string DirectoryPath) =>
        Directory.Exists(Path.Combine(DirectoryPath, "profiles")) ||
        File.Exists(Path.Combine(DirectoryPath, "connections.sqlite3")) ||
        File.Exists(Path.Combine(DirectoryPath, "active-release.txt"));

    public static string StateRoot(string Root)
    {
        // Updates installed into an older bundle must keep its existing profiles.
        string Legacy = Path.Combine(Root, "Production", "launcher");
        return !HasState(Root) && HasState(Legacy) ? Legacy : Root;
    }

    public static string AdapterRuntime(string Root)
    {
        string Bundled = Path.Combine(StateRoot(Root), "adapter");
        string Prepared = Path.Combine(Root, ".cache", "adapters", "linux");
        return Directory.Exists(Bundled) || !Directory.Exists(Prepared) ? Bundled : Prepared;
    }
}
