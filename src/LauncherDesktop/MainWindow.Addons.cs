using Avalonia.Controls;
using Avalonia.Interactivity;
using Division.Launcher.Addons;

namespace Division.Launcher.Desktop;

public sealed partial class MainWindow
{
    private AddonCatalog AddonSettings => new(Root, Path.Combine(SettingsDirectory, "addons.sqlite3"));

    private AddonSelection EnabledAddons() => Connection is null ? new([]) : AddonSettings.Selection(Connection.Id);

    private void RescanAddons(object? Sender, RoutedEventArgs Event) => RefreshAddons();

    private void RefreshAddons()
    {
        AddonList.Children.Clear();
        if (Connection is null) { AddonHint.Text = "Choose a server to configure its addons."; return; }
        Guid Server = Connection.Id;
        AddonCatalog Catalog = AddonSettings;
        foreach (AddonDefinition Definition in Catalog.Discover())
        {
            CheckBox Toggle = new() { Content = Definition.Name, IsChecked = Catalog.Enabled(Server, Definition.Id) };
            Toggle.IsCheckedChanged += (_, _) =>
            {
                try { Catalog.SetEnabled(Server, Definition.Id, Toggle.IsChecked == true); Status("Addon preference saved for the next launch."); }
                catch (Exception Error) when (Error is IOException or Microsoft.Data.Sqlite.SqliteException or ArgumentException) { Status(Error.Message, true); }
            };
            TextBlock Description = new() { Text = Definition.Description }; Description.Classes.Add("Muted");
            AddonList.Children.Add(new StackPanel { Spacing = 4, Children = { Toggle, Description } });
        }
        AddonHint.Text = "Built-in panels render inside the game. Optional Lua observers are discovered in addons; install only scripts you trust.";
    }
}
