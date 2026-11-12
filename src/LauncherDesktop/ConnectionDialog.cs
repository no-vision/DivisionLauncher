using Avalonia;
using Avalonia.Controls;
using Avalonia.Layout;
using Avalonia.Media;
using Division.Core.Launcher;

namespace Division.Launcher.Desktop;

/// <summary>Server discovery sends no account credentials. Editing retains the pinned realm.</summary>
public sealed class ConnectionDialog : Window
{
    public ConnectionDialog(ServerConnection? Existing)
    {
        Title = Existing is null ? "Add a private server" : "Edit connection";
        Width = 560; Height = 520; CanResize = false; WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = Brush.Parse("#18232D");
        TextBox Label = new() { Text = Existing?.Label ?? "", PlaceholderText = "My community" };
        TextBox Address = new() { Text = Existing?.ApiUrl ?? "", PlaceholderText = "server.example.com:25443" };
        TextBox Fingerprint = new() { Text = Existing?.CertificateSha256 ?? "", PlaceholderText = "SHA256 fingerprint from the server administrator", TextWrapping = TextWrapping.Wrap };
        TextBlock Message = new() { Text = "Each server has its own accounts and characters.", TextWrapping = TextWrapping.Wrap, Foreground = Brush.Parse("#AEBBC9") };
        Button Save = new() { Content = "Verify and save", Classes = { "Primary" } };
        Button Cancel = new() { Content = "Cancel" }; Cancel.Click += (_, _) => Close();
        bool Checking = false;
        Save.Click += async (_, _) =>
        {
            if (Checking) return;
            Checking = true; Save.IsEnabled = false; Cancel.IsEnabled = false;
            Message.Text = "Checking server identity…";
            try
            {
                string Url = ServerClient.NormalizeAddress(Address.Text ?? "");
                string? Pin = string.IsNullOrWhiteSpace(Fingerprint.Text) ? null : Fingerprint.Text.Trim();
                using ServerClient Client = new(Url, Pin, Existing?.RealmId);
                ServerDescriptor Descriptor = await Client.Verify();
                string Name = string.IsNullOrWhiteSpace(Label.Text) ? Descriptor.Name : Label.Text.Trim();
                Checking = false;
                Close(new ServerConnection(Existing?.Id ?? Guid.NewGuid(), Name, Url, Descriptor.RealmId, Pin, Existing?.LastUsername));
            }
            catch (Exception Error) when (Error is not OutOfMemoryException)
            {
                Message.Text = Error is HttpRequestException ? "Could not verify this server. Check the address and the administrator's certificate fingerprint." : Error.Message;
                Message.Foreground = Brush.Parse("#FFA7A0");
            }
            finally { Checking = false; Save.IsEnabled = true; Cancel.IsEnabled = true; }
        };
        Closing += (_, Event) => { if (Checking) Event.Cancel = true; };
        Content = new StackPanel { Margin = new Thickness(28), Spacing = 12, Children =
        {
            new TextBlock { Text = Title, FontSize = 24, FontWeight = FontWeight.SemiBold },
            new TextBlock { Text = "Display name · optional" }, Label,
            new TextBlock { Text = "Server host or HTTPS address" }, Address,
            new Expander { Header = "Certificate for a privately signed server", IsExpanded = Existing?.CertificateSha256 is not null,
                Content = new StackPanel { Spacing = 8, Children = { new TextBlock { Text = "Leave blank for a publicly trusted HTTPS certificate.", TextWrapping = TextWrapping.Wrap }, Fingerprint } } },
            Message,
            new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12, Margin = new Thickness(0,12,0,0), Children = { Save, Cancel } }
        } };
    }
}

internal static class Dialogs
{
    public static Task<bool> Confirm(Window Owner, string Title, string Message)
    {
        Window Dialog = new() { Title = Title, Width = 480, Height = 230, CanResize = false, WindowStartupLocation = WindowStartupLocation.CenterOwner };
        Button Confirm = new() { Content = "Remove", Classes = { "Primary" } };
        Button Cancel = new() { Content = "Cancel" };
        Confirm.Click += (_, _) => Dialog.Close(true); Cancel.Click += (_, _) => Dialog.Close(false);
        Dialog.Content = new StackPanel { Margin = new Thickness(24), Spacing = 20, Children =
        {
            new TextBlock { Text = Message, TextWrapping = TextWrapping.Wrap },
            new StackPanel { Orientation = Orientation.Horizontal, Spacing = 12, Children = { Confirm, Cancel } }
        } };
        return Dialog.ShowDialog<bool>(Owner);
    }
}
