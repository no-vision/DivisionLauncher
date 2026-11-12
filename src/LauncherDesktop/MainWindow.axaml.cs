using Avalonia.Controls;
using Avalonia.Interactivity;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using Division.Core.Launcher;

namespace Division.Launcher.Desktop;

public sealed partial class MainWindow : Window
{
    private readonly string Root;
    private readonly ConnectionStore Store;
    private readonly GameDisplayStore DisplayStore;
    private readonly string SettingsDirectory;
    private ServerClient? Client;
    private ServerConnection? Connection;
    private ServerDescriptor? Descriptor;
    private AccountProfile? Profile;
    private Bitmap? Picture;
    private bool Busy;
    private bool Refreshing;
    private bool GameRunning;
    private bool ClosingAfterLogout;

    public MainWindow() : this(Program.Root, Program.StorePath) { }

    public MainWindow(string Root, string StorePath)
    {
        InitializeComponent();
        this.Root = Root; Store = new(StorePath); SettingsDirectory = Path.GetDirectoryName(Path.GetFullPath(StorePath))!;
        DisplayStore = new(StorePath);
        GameInstallation Installation = Store.Installation() ?? new("", "",
            LauncherPaths.AdapterRuntime(Root));
        GameFolder.Text = Installation.GameDirectory; ProtonFolder.Text = Installation.ProtonDirectory; AdapterFolder.Text = Installation.FridaDirectory;
        LoadDisplaySettings();
        GameFolder.LostFocus += (_, _) => LoadDisplaySettings();
        if (OperatingSystem.IsWindows())
        {
            PlatformHint.Text = "Choose your installed game folder. Windows launches natively; the adapter runtime is bundled. Game dependencies and graphics drivers must be installed.";
            ProtonLabel.IsVisible = ProtonControls.IsVisible = AdapterSettings.IsVisible = false;
        }
        else if (!OperatingSystem.IsLinux())
        {
            PlatformHint.Text = "Account management works on this platform. Game launching currently requires our Linux/Proton adapter.";
            PlayButton.IsEnabled = false;
        }
        LauncherVersionText.Text = "Version " + LauncherRelease.CurrentVersion;
        RefreshServers();
        Closing += OnClosing;
    }

    #region Async Operations And Session Lifetime

    private void Status(string Message, bool Error = false)
    {
        StatusText.Text = Message; if (DialogStatus is not null) DialogStatus.Text = Message;
        StatusText.Foreground = Error ? Brush.Parse("#FFA7A0") : Brush.Parse("#DCE6EE");
    }

    private async Task Execute(string Message, Func<Task> Action)
    {
        if (Busy) return;
        Busy = true; if (ActiveDialog is not null) ActiveDialog.IsEnabled = false; Workspace.IsEnabled = false; BusyIndicator.IsVisible = true; Status(Message);
        try { await Action(); }
        catch (LauncherSessionExpiredException Error)
        {
            await ClearSession();
            Status(Error.Message, true);
        }
        catch (Exception Error) when (Error is not OutOfMemoryException)
        {
            Status(FriendlyError(Error), true);
        }
        finally { Busy = false; if (ActiveDialog is not null) ActiveDialog.IsEnabled = true; Workspace.IsEnabled = true; BusyIndicator.IsVisible = false; UpdateGameControls(); }
    }

    private static string FriendlyError(Exception Error) => Error switch
    {
        HttpRequestException => "Cannot reach or verify this server. Check its address, connection and certificate fingerprint.",
        TaskCanceledException => "The server took too long to respond. Please try again.",
        _ when Error.Message.Contains("credentials", StringComparison.OrdinalIgnoreCase) => "The username or password was not accepted.",
        _ when Error.Message.Contains("account_unavailable", StringComparison.OrdinalIgnoreCase) => "That username is already registered on this server.",
        _ when Error.Message.Contains("invalid_account_name", StringComparison.OrdinalIgnoreCase) => "Use 3–32 letters, numbers, underscores, dots or hyphens for the username.",
        _ when Error.Message.Contains("invalid_email_address", StringComparison.OrdinalIgnoreCase) => "Enter a valid recovery email address, or leave it blank.",
        _ => Error.Message
    };

    private async Task ClearSession()
    {
        ServerClient? Previous = Client;
        Client = null; Profile = null; Descriptor = null;
        ClearFriends();
        ProfileSection.IsVisible = false; LoginSection.IsVisible = true; Picture?.Dispose(); Picture = null; Avatar.Source = null;
        LoginPassword.Text = ""; NewPassword.Text = ""; ConfirmPassword.Text = "";
        RecoveryCode.Text = ""; ResetPassword.Text = ""; ConfirmResetPassword.Text = "";
        ProfileGamertag.Text = ""; ProfileName.Text = ""; ProfileEmail.Text = "";
        if (Previous is null) return;
        try { await Previous.Logout(); }
        catch (Exception Error) when (Error is HttpRequestException or InvalidOperationException or TaskCanceledException) { }
        finally { Previous.Dispose(); }
    }

    private async void OnClosing(object? Sender, WindowClosingEventArgs Event)
    {
        if (ClosingAfterLogout) return;
        Event.Cancel = true;
        if (GameRunning || Busy) { Status("Finish the current operation or close the game before closing the launcher."); return; }
        await Execute("Signing out…", async () => { await ClearSession(); ClosingAfterLogout = true; Close(); });
    }

    private ServerClient RequireClient() => Client ?? throw new InvalidOperationException("Choose a server first.");

    private async Task BeginAuthentication()
    {
        if (Connection is null) throw new InvalidOperationException("Choose a server first.");
        await ClearSession();
        Client = new(Connection.ApiUrl, Connection.CertificateSha256, Connection.RealmId);
    }

    #endregion

    #region Server Connections

    private void RefreshServers(Guid? Selected = null)
    {
        Refreshing = true; ServerConnection[] Entries = Store.List(); Servers.ItemsSource = Entries;
        Servers.SelectedItem = Entries.FirstOrDefault(Value => Value.Id == Selected) ?? Entries.FirstOrDefault();
        Refreshing = false;
        EditServerButton.IsEnabled = RemoveServerButton.IsEnabled = Entries.Length > 0;
        AccountTab.IsEnabled = Entries.Length > 0;
        if (Servers.SelectedItem is ServerConnection)
            Dispatcher.UIThread.Post(async () => await SelectCurrentServer());
        else { ServerTitle.Text = "Welcome"; ServerDetail.Text = "Add a private server to create an account or sign in."; }
    }

    private async void ServerSelected(object? Sender, SelectionChangedEventArgs Event) => await SelectCurrentServer();

    private async Task SelectCurrentServer()
    {
        if (Refreshing || Servers.SelectedItem is not ServerConnection Selected || Busy || GameRunning) return;
        await Execute("Connecting to " + Selected.Label + "…", async () =>
        {
            await ClearSession(); Connection = Selected;
            NewUsername.Text = ""; NewGamertag.Text = ""; NewName.Text = ""; NewEmail.Text = ""; RecoveryIdentity.Text = "";
            Client = new(Selected.ApiUrl, Selected.CertificateSha256, Selected.RealmId);
            ServerTitle.Text = Selected.Label; ServerDetail.Text = Selected.ApiUrl; Pages.SelectedItem = AccountTab;
            RefreshAccounts(); RefreshAddons(); LoginUsername.Text = Selected.LastUsername ?? "";
            Descriptor = await Client.Verify(); ServerTitle.Text = Descriptor.Name;
            OpenRegistrationButton.IsEnabled = Descriptor.RegistrationEnabled;
            RecoveryButton.IsEnabled = Descriptor.RecoveryDeliveryEnabled;
            RecoveryHint.Text = Descriptor.RecoveryDeliveryEnabled ? "The server sends a reset code to your saved email address." : "This server has not configured email recovery delivery.";
            Status("Connected. Sign in or create your own account.");
        });
    }

    private async void CheckConnection(object? Sender, RoutedEventArgs Event) => await Execute("Checking server and game ports…", async () =>
    {
        EndpointCheck[] Checks = await RequireClient().CheckConnection();
        ConnectionPreflight.RequireReachable(Checks);
        Status("Server identity, launcher compatibility and all four game TCP ports verified. Gameplay has not been tested.");
    });

    private async void AddServer(object? Sender, RoutedEventArgs Event)
    {
        ServerConnection? Saved = await new ConnectionDialog(null).ShowDialog<ServerConnection?>(this);
        if (Saved is null) return;
        await Execute("Saving server…", () => { Store.Save(Saved); return Task.CompletedTask; });
        RefreshServers(Saved.Id);
    }

    private async void EditServer(object? Sender, RoutedEventArgs Event)
    {
        if (Connection is null) return;
        ServerConnection? Saved = await new ConnectionDialog(Connection).ShowDialog<ServerConnection?>(this);
        if (Saved is null) return;
        await Execute("Saving connection…", async () => { await ClearSession(); Store.Save(Saved); });
        RefreshServers(Saved.Id);
    }

    private async void RemoveServer(object? Sender, RoutedEventArgs Event)
    {
        if (Connection is null || !await Dialogs.Confirm(this, "Remove connection?", "Remove this server and its saved usernames from this launcher? Accounts and characters on the server are kept.")) return;
        await Execute("Removing connection…", async () => { await ClearSession(); Store.Remove(Connection.Id); Connection = null; });
        RefreshServers();
    }

    #endregion

    #region Accounts And Registration

    private void RefreshAccounts()
    {
        AccountChoices.ItemsSource = Connection is null ? Array.Empty<SavedAccount>() : Store.Accounts(Connection.Id);
    }

    private void AccountSelected(object? Sender, SelectionChangedEventArgs Event)
    {
        if (AccountChoices.SelectedItem is SavedAccount Selected) { LoginUsername.Text = Selected.Username; LoginPassword.Text = ""; }
    }

    private void ForgetAccount(object? Sender, RoutedEventArgs Event)
    {
        if (AccountChoices.SelectedItem is not SavedAccount Selected) return;
        Store.Forget(Selected.ServerId, Selected.AccountId);
        if (Connection?.LastUsername == Selected.Username) { Connection = Connection with { LastUsername = null }; Store.Save(Connection); }
        RefreshAccounts(); LoginUsername.Text = ""; LoginPassword.Text = ""; Status("Saved username removed. The server account is unchanged.");
    }

    private async Task SignedIn(AccountProfile Account)
    {
        Profile = Account;
        Descriptor = await RequireClient().Verify();
        FriendsTab.IsVisible = true;
        FriendControls.IsEnabled = FriendsFirst.IsEnabled = Descriptor.PrivateFriendsSupported;
        FriendsHint.Text = Descriptor.PrivateFriendsSupported ? "Loading friends…" :
            "This server needs an update to support private friends. Select a current server or ask its operator to update it.";
        if (RememberAccount.IsChecked == true && Connection is not null)
        {
            Store.Remember(Connection.Id, Account); Connection = Connection with { LastUsername = Account.Username }; Store.Save(Connection); RefreshAccounts();
        }
        ShowProfile(); ProfileSection.IsVisible = true; LoginSection.IsVisible = false; Pages.SelectedItem = AccountTab; CompleteDialog();
        if (Descriptor.PrivateFriendsSupported)
        {
            try { await LoadFriends(); }
            catch (LauncherSessionExpiredException) { throw; }
            catch (Exception Error) when (Error is HttpRequestException or InvalidOperationException or TaskCanceledException)
            { FriendsHint.Text = "Could not load friends. Use Refresh to retry. " + FriendlyError(Error); }
        }
        try { await LoadPicture(); }
        catch (Exception Error) when (Error is HttpRequestException or ArgumentException or InvalidOperationException or TaskCanceledException)
        { Status("Signed in. Your profile picture could not be loaded."); return; }
        Status("Signed in as " + Account.Gamertag + ". Choose Play when ready.");
    }

    private async void SignIn(object? Sender, RoutedEventArgs Event) => await Execute("Signing in…", async () =>
    {
        string Password = LoginPassword.Text ?? ""; LoginPassword.Text = "";
        await BeginAuthentication();
        await SignedIn(await RequireClient().Login(new((LoginUsername.Text ?? "").Trim(), Password)));
    });

    private async void Register(object? Sender, RoutedEventArgs Event) => await Execute("Creating your account…", async () =>
    {
        string Password = NewPassword.Text ?? "";
        if (Password.Length < 8) throw new ArgumentException("Use a password with at least eight characters.");
        if (Password != ConfirmPassword.Text) throw new ArgumentException("The passwords do not match.");
        string Username = (NewUsername.Text ?? "").Trim();
        string Gamertag = string.IsNullOrWhiteSpace(NewGamertag.Text) ? Username : NewGamertag.Text.Trim();
        AccountRegistration Registration = new(Username, Password, Gamertag, NewName.Text, NewEmail.Text);
        bool Created = false;
        try
        {
            await BeginAuthentication();
            await RequireClient().Register(Registration); Created = true;
            LoginUsername.Text = Username;
            await SignedIn(await RequireClient().Login(new(Username, Password)));
            NewUsername.Text = ""; NewGamertag.Text = ""; NewName.Text = ""; NewEmail.Text = "";
        }
        catch (Exception Error) when (Created && Error is not OutOfMemoryException)
        { Pages.SelectedItem = AccountTab; throw new InvalidOperationException("Your account was created. Please sign in: " + FriendlyError(Error)); }
        finally { NewPassword.Text = ""; ConfirmPassword.Text = ""; }
    });

    private void ShowProfile()
    {
        if (Profile is null) return;
        ProfileHeading.Text = Profile.Gamertag; ProfileUsername.Text = "@" + Profile.Username;
        ProfileGamertag.Text = Profile.Gamertag; ProfileName.Text = Profile.Name ?? ""; ProfileEmail.Text = Profile.Email ?? "";
    }

    private async Task LoadPicture()
    {
        Picture?.Dispose(); Picture = null; Avatar.Source = null;
        if (Profile?.PictureSha256 is null) return;
        byte[]? Bytes = await RequireClient().PictureBytes(Profile.AccountId);
        if (Bytes is null) return;
        using MemoryStream Input = new(Bytes); Picture = new Bitmap(Input); Avatar.Source = Picture;
    }

    private async void SaveProfile(object? Sender, RoutedEventArgs Event) => await Execute("Saving profile…", async () =>
    {
        Profile = await RequireClient().Update(new(ProfileGamertag.Text ?? "", ProfileName.Text, ProfileEmail.Text));
        if (Connection is not null && Store.Accounts(Connection.Id).Any(Value => Value.AccountId == Profile.AccountId)) Store.Remember(Connection.Id, Profile);
        ShowProfile(); Status("Profile saved on this server.");
    });

    private async void ChangePicture(object? Sender, RoutedEventArgs Event)
    {
        IReadOnlyList<IStorageFile> Files = await StorageProvider.OpenFilePickerAsync(new() { Title = "Choose a profile picture", AllowMultiple = false,
            FileTypeFilter = [new FilePickerFileType("PNG image") { Patterns = ["*.png"] }] });
        string? Path = Files.FirstOrDefault()?.TryGetLocalPath(); if (Path is null) return;
        await Execute("Uploading picture…", async () => { Profile = await RequireClient().Picture(Path); await LoadPicture(); Status("Profile picture saved."); });
    }

    private async void SignOut(object? Sender, RoutedEventArgs Event)
    {
        if (GameRunning) { Status("Close the game before signing out."); return; }
        await Execute("Signing out…", ClearSession);
        if (Connection is not null) RefreshServers(Connection.Id);
    }

    #endregion

    #region Recovery

    private async void RequestRecovery(object? Sender, RoutedEventArgs Event) => await Execute("Requesting recovery…", async () =>
    {
        await RequireClient().Recovery(RecoveryIdentity.Text ?? ""); Status("If an account matches, the server will send recovery instructions.");
    });

    private async void CompleteRecovery(object? Sender, RoutedEventArgs Event) => await Execute("Resetting password…", async () =>
    {
        if ((ResetPassword.Text ?? "").Length < 8 || ResetPassword.Text != ConfirmResetPassword.Text)
            throw new ArgumentException("Enter matching passwords with at least eight characters.");
        try { await RequireClient().Reset(new(RecoveryCode.Text ?? "", ResetPassword.Text!)); }
        finally { RecoveryCode.Text = ""; ResetPassword.Text = ""; ConfirmResetPassword.Text = ""; }
        CompleteDialog(); Pages.SelectedItem = AccountTab; Status("Password reset. Sign in with your new password.");
    });

    #endregion

    #region Installation And Play

    private GameInstallation Installation() => new((GameFolder.Text ?? "").Trim(), (ProtonFolder.Text ?? "").Trim(), (AdapterFolder.Text ?? "").Trim(), ReadDisplaySettings());

    private async Task Browse(TextBox Field)
    {
        IReadOnlyList<IStorageFolder> Folders = await StorageProvider.OpenFolderPickerAsync(new() { Title = "Choose a folder", AllowMultiple = false });
        string? Path = Folders.FirstOrDefault()?.TryGetLocalPath(); if (Path is not null) Field.Text = Path;
    }
    private async void BrowseGame(object? Sender, RoutedEventArgs Event) { await Browse(GameFolder); LoadDisplaySettings(); }
    private async void BrowseProton(object? Sender, RoutedEventArgs Event) => await Browse(ProtonFolder);
    private async void BrowseAdapter(object? Sender, RoutedEventArgs Event) => await Browse(AdapterFolder);
    private async void SaveInstallation(object? Sender, RoutedEventArgs Event) => await Execute("Checking installation…", async () =>
    {
        GameInstallation Value = Installation(); await InstallationPreflight.Check(Root, Value); Store.SetInstallation(Value);
        DisplayStore.Save(Value.GameDirectory, Value.Display!);
        Status("Installation verified and saved.");
    });

    private void UpdateGameControls()
    {
        PlayButton.IsEnabled = !GameRunning;
        SignOutButton.IsEnabled = !GameRunning;
        ServerPanel.IsEnabled = !GameRunning;
    }

    private async void Play(object? Sender, RoutedEventArgs Event)
    {
        if (GameRunning || Busy) return;
        Task<int>? Running = null;
        await Execute("Starting your game…", () =>
        {
            GameInstallation Value = Installation(); GameLauncher.ValidateInstallation(Value); Store.SetInstallation(Value);
            DisplayStore.Save(Value.GameDirectory, Value.Display!);
            ServerClient Server = RequireClient();
            GameRunning = true;
            Running = new GameLauncher(Root).Run(Server, Value, EnabledAddons(),
                Message => Dispatcher.UIThread.Post(() => Status(Message)));
            return Task.CompletedTask;
        });
        if (Running is null) return;
        try
        {
            int ExitCode = await Running;
            Status(ExitCode switch {
                0 => "Game closed. You can play again or sign out.",
                20 => "The installed game requested a Steam relaunch. See client-adapter.log for the startup trace.",
                _ => $"The game adapter exited with code {ExitCode}. Check runtime.log and client-adapter.log in the run folder."
            }, ExitCode != 0);
        }
        catch (Exception Error) when (Error is not OutOfMemoryException) { Status(FriendlyError(Error), true); }
        finally { GameRunning = false; UpdateGameControls(); }
    }

    #endregion
}
