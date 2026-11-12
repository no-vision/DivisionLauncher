using Avalonia.Interactivity;
using Division.Launcher;

namespace Division.Launcher.Desktop;

public sealed partial class MainWindow
{
    private string? LoadedDisplayDirectory;

    private void LoadDisplaySettings()
    {
        string DirectoryName = (GameFolder.Text ?? "").Trim();
        if (DirectoryName.Length == 0 || DirectoryName == LoadedDisplayDirectory) return;
        try
        {
            GameDisplaySettings Value = DisplayStore.Read(DirectoryName);
            DisplayMode.SelectedIndex = Value.Mode switch { "windowed" => 1, "fullscreen" => 2, _ => 0 };
            DisplayWidth.Value = Value.Width; DisplayHeight.Value = Value.Height;
            LoadedDisplayDirectory = DirectoryName;
        }
        catch (Exception Error) when (Error is ArgumentException or IOException or System.Text.Json.JsonException)
        { Status("Cannot load display settings: " + Error.Message); }
    }

    private GameDisplaySettings ReadDisplaySettings()
    {
        GameDisplaySettings Value = new(DisplayMode.SelectedIndex switch { 1 => "windowed", 2 => "fullscreen", _ => "default" },
            checked((int)(DisplayWidth.Value ?? 1280)), checked((int)(DisplayHeight.Value ?? 720)));
        Value.Validate(); return Value;
    }

    private async void SaveDisplaySettings(object? Sender, RoutedEventArgs Event) => await Execute("Saving display settings…", () =>
    {
        string DirectoryName = (GameFolder.Text ?? "").Trim();
        if (DirectoryName.Length == 0) throw new ArgumentException("Choose a game folder first.");
        DisplayStore.Save(DirectoryName, ReadDisplaySettings());
        Status("Display settings saved for this installation and launcher profile. They apply on the next launch.");
        return Task.CompletedTask;
    });
}
