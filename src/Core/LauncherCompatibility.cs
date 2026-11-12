namespace Division.Core.Launcher;

/// <summary>Build supported by the bundled native startup adapter. SHA256 was
/// measured from the locally verified retail executable, not from account data.
/// No game executable is distributed with the launcher.</summary>
public static class LauncherCompatibility
{
    public const string GameBuild = "retail-pc-31c74abfedb5";
    public const string ExecutableSha256 = "31c74abfedb52fa2ef8342e8f434d766184eca32d85ea9419bcbb46c09a6e379";
}
