using Avalonia.Interactivity;
using Avalonia.Platform.Storage;

namespace Division.Launcher.Desktop;

public sealed partial class MainWindow
{
    private async void InstallUpdate(object? Sender, RoutedEventArgs Event)
    {
        if (GameRunning || Busy) { Status("Close the game before installing a launcher update."); return; }
        IReadOnlyList<IStorageFile> Files = await StorageProvider.OpenFilePickerAsync(new()
        { Title = "Choose a trusted launcher release", AllowMultiple = false,
            FileTypeFilter = [new FilePickerFileType("Launcher release") { Patterns = ["*.zip"] }] });
        string? Package = Files.FirstOrDefault()?.TryGetLocalPath(); if (Package is null) return;
        await Execute("Inspecting release…", async () =>
        {
            ReleaseManifest Manifest = LauncherRelease.Inspect(Package);
            if (!await Dialogs.Confirm(this, "Install launcher " + Manifest.Version + "?",
                "Install this package only if you trust its distributor. File checksums detect damage but do not verify the publisher. The update activates when you reopen the launcher.")) return;
            string Installed = await Task.Run(() => new LauncherRelease(Root).Install(Package));
            UpdateStatus.Text = Installed + " is ready. Close and reopen using launch.sh or launch.ps1.";
            Status("Launcher update installed for the next start.");
        });
    }

    private void UseBundledLauncher(object? Sender, RoutedEventArgs Event)
    {
        if (Busy || GameRunning) return;
        try { new LauncherRelease(Root).UseBundled(); UpdateStatus.Text = "The bundled launcher will run on the next start."; }
        catch (IOException Error) { Status(Error.Message, true); }
    }
}
