using System.Diagnostics;
using System.Security.Cryptography;
using Division.Core.Launcher;

namespace Division.Launcher;

public static class InstallationPreflight
{
    public static async Task Check(string Root, GameInstallation Installation)
    {
        GameLauncher.ValidateInstallation(Installation);
        string Adapter = GameLauncher.AdapterFile(Root);
        if (!File.Exists(Adapter)) throw new InvalidOperationException("The launcher package is missing its game adapter. Reinstall the complete launcher bundle.");
        await using (FileStream Executable = File.OpenRead(Path.Combine(Installation.GameDirectory, "thedivision.exe")))
        {
            string Hash = Convert.ToHexStringLower(await SHA256.HashDataAsync(Executable));
            if (Hash != LauncherCompatibility.ExecutableSha256)
                throw new InvalidOperationException("Your game executable is not the build supported by this launcher. Use the supported official installation or obtain an updated launcher adapter. Your game files were not changed.");
        }
        ProcessStartInfo Start = new(GameLauncher.AdapterPython(Root)) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true };
        GameLauncher.AddAdapterArguments(Start, Root, Installation);
        Start.ArgumentList.Add("--check");
        using Process Process = new() { StartInfo = Start };
        try { Process.Start(); }
        catch (System.ComponentModel.Win32Exception)
        { throw new InvalidOperationException("The game adapter runtime could not start. On Windows, extract the full launcher ZIP; on Linux, install Python 3."); }
        Task<string> Output = Process.StandardOutput.ReadToEndAsync();
        Task<string> Error = Process.StandardError.ReadToEndAsync();
        using CancellationTokenSource Timeout = new(TimeSpan.FromSeconds(60));
        try { await Process.WaitForExitAsync(Timeout.Token); }
        catch (OperationCanceledException)
        {
            Process.Kill(true); await Process.WaitForExitAsync();
            throw new InvalidOperationException("Installation checks timed out. Verify the game drive and adapter dependencies.");
        }
        string Detail = (await Output + "\n" + await Error).Trim();
        if (Process.ExitCode != 0)
            throw new InvalidOperationException("Installation needs attention:\n" + Detail[..Math.Min(Detail.Length, 2000)]);
    }
}
