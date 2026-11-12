using Avalonia;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media;

namespace Division.Launcher.Desktop;

public sealed partial class MainWindow
{
    private Window? ActiveDialog;
    private TextBlock? DialogStatus;
    private bool DialogCompleted;

    private async void OpenRegistration(object? Sender, RoutedEventArgs Event)
        => await ShowAccountDialog("Create account", RegistrationForm);

    private async void OpenRecovery(object? Sender, RoutedEventArgs Event)
        => await ShowAccountDialog("Recover account", RecoveryForm);

    // Forms share the main window's connection/session lifetime and named controls,
    // but have a separate modal visual tree while open. Never open two forms.
    private async Task ShowAccountDialog(string Title, ScrollViewer Form)
    {
        if (ActiveDialog is not null || Busy || Profile is not null || Connection is null) return;
        DialogForms.Children.Remove(Form);
        DialogStatus = new() { TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 16, 0, 0) };
        Grid Body = new() { RowDefinitions = new RowDefinitions("*,Auto"), Margin = new Thickness(24) };
        Body.Children.Add(Form); Grid.SetRow(DialogStatus, 1); Body.Children.Add(DialogStatus);
        Window Dialog = new() { Title = Title + " — " + Connection.Label, Width = 560, Height = 700,
            MinWidth = 460, MinHeight = 540, Content = Body, Background = Brush.Parse("#101820"),
            WindowStartupLocation = WindowStartupLocation.CenterOwner };
        ActiveDialog = Dialog; DialogCompleted = false;
        Dialog.Closing += (_, Event) => { if (Busy && !DialogCompleted) Event.Cancel = true; };
        try { await Dialog.ShowDialog(this); }
        finally
        {
            Body.Children.Remove(Form); DialogForms.Children.Add(Form);
            NewPassword.Text = ConfirmPassword.Text = RecoveryCode.Text = ResetPassword.Text = ConfirmResetPassword.Text = "";
            ActiveDialog = null; DialogStatus = null;
        }
    }

    private void CompleteDialog()
    {
        DialogCompleted = true;
        ActiveDialog?.Close();
    }
}
