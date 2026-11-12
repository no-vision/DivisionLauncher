using Avalonia;
using Avalonia.Controls;
using Avalonia.Headless;
using Avalonia.Interactivity;
using Avalonia.VisualTree;
using Division.Core.Launcher;
using Division.Launcher;
using Division.Launcher.Desktop;

namespace Division.Launcher.Desktop.Tests;

internal static class Program
{
    public static AppBuilder BuildAvaloniaApp() => AppBuilder.Configure<App>().UseSkia()
        .UseHeadless(new AvaloniaHeadlessPlatformOptions { UseHeadlessDrawing = false });

    public static async Task Main(string[] Args)
    {
        if (Args.Length == 1 && Args[0] == "--remote-local") { await RemoteLauncherChecks.Run(); return; }
        if (Args.Length == 2 && Args[0] == "--launcher-local") { LauncherLocalChecks.Run(Args[1]); return; }
        if (Args.Length != 3) throw new ArgumentException("Usage: <workspace root> <isolated HTTPS URL> <certificate SHA256>");
        string Root = Path.GetFullPath(Args[0]);
        string DirectoryName = Path.Combine(Root, ".cache/tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(DirectoryName);
        string StorePath = Path.Combine(DirectoryName, "connections.sqlite3");
        ConnectionStore Store = new(StorePath);
        using ServerClient Probe = new(Args[1], Args[2]);
        ServerDescriptor Descriptor = await Probe.Verify();
        ServerConnection Connection = new(Guid.NewGuid(), "Isolated UI test", Args[1], Descriptor.RealmId, Args[2], null);
        Store.Save(Connection);
        string Username = "desktop" + Guid.NewGuid().ToString("N")[..12];
        string Password = Guid.NewGuid().ToString("N");
        int Checks = 0;
        void Check(bool Result, string Message) { if (!Result) throw new InvalidOperationException(Message); Checks++; }
        // Dispatch completion can inline this continuation on the UI worker.
        // Async disposal avoids synchronously waiting for that same worker.
        await using HeadlessUnitTestSession Session = HeadlessUnitTestSession.StartNew(typeof(Program));
        await Session.Dispatch(async () =>
        {
            MainWindow Window = new(Root, StorePath); Window.Show();
            T Control<T>(string Name) where T : Control => Window.FindControl<T>(Name) ?? throw new InvalidOperationException(Name);
            void Fill(string Name, string Value) => Control<TextBox>(Name).Text = Value;
            void Click(string Name) => Control<Button>(Name).RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
            async Task Idle()
            {
                await Task.Delay(80);
                DateTimeOffset Deadline = DateTimeOffset.UtcNow.AddSeconds(30);
                while (!Control<Grid>("Workspace").IsEnabled && DateTimeOffset.UtcNow < Deadline) await Task.Delay(30);
                Check(Control<Grid>("Workspace").IsEnabled, "GUI operation timed out");
            }
            await Idle();
            Check(Control<TextBlock>("StatusText").Text!.StartsWith("Connected.", StringComparison.Ordinal), "server discovery must succeed: " + Control<TextBlock>("StatusText").Text);
            Check(!Control<ScrollViewer>("ProfileSection").IsVisible && Control<ScrollViewer>("LoginSection").IsVisible, "signed-out view on startup");
            Click("OpenRegistrationButton"); await Task.Delay(80);
            Fill("NewUsername", Username); Fill("NewGamertag", "Desktop Test");
            Fill("NewPassword", Password); Fill("ConfirmPassword", "different");
            Click("RegisterButton"); await Idle();
            Check(!Control<ScrollViewer>("ProfileSection").IsVisible, "mismatched password confirmation cannot authenticate");
            Fill("ConfirmPassword", Password); Click("RegisterButton"); await Idle();
            Check(Control<ScrollViewer>("ProfileSection").IsVisible, "registration signs into real account API");
            Check(Control<TextBox>("NewPassword").Text == "" && Control<TextBox>("ConfirmPassword").Text == "", "secrets cleared after registration");
            Check(Store.Accounts(Connection.Id).Single().Username == Username, "local account identity is persisted");
            using ServerClient Verify = new(Args[1], Args[2], Descriptor.RealmId);
            AccountProfile Profile = await Verify.Login(new(Username, Password));
            Check(Profile.Gamertag == "Desktop Test", "GUI created real server profile");

            Check(Control<TabItem>("FriendsTab").IsVisible, "signed-in Friends interface is visible even for older servers");
            if (Descriptor.PrivateFriendsSupported)
            {
                Check(Control<TextBlock>("FriendsHint").Text!.Contains("No friends"), "friends load automatically on login");
                using ServerClient Peer = new(Args[1], Args[2], Descriptor.RealmId);
                string PeerName = "friend" + Guid.NewGuid().ToString("N")[..12];
                AccountProfile PeerProfile = await Peer.Register(new(PeerName, Password));
                await Peer.Login(new(PeerName, Password));
                Control<TabControl>("Pages").SelectedItem = Control<TabItem>("FriendsTab");
                Fill("FriendUsername", PeerName); Click("FindFriendButton"); await Idle();
                void FriendAction(string Label) => Control<StackPanel>("FriendsList").GetVisualDescendants()
                    .OfType<Button>().Single(Button => Button.Content?.ToString() == Label)
                    .RaiseEvent(new RoutedEventArgs(Button.ClickEvent));
                FriendAction("Send friend request"); await Idle();
                Check((await Peer.Friends()).Entries.Single().AccountId == Profile.AccountId, "GUI request reaches second private account");
                await Peer.AcceptFriend(Profile.AccountId);
                Click("RefreshFriendsButton"); await Idle();
                Check(Control<StackPanel>("FriendsList").GetVisualDescendants().OfType<TextBlock>().Any(Text => Text.Text == "Friends"), "GUI shows accepted friendship");
                using (Avalonia.Media.Imaging.WriteableBitmap? Frame = Window.CaptureRenderedFrame())
                    Frame?.Save(Path.Combine(DirectoryName, "friends.png"), Avalonia.Media.Imaging.PngBitmapEncoderOptions.Default);
                FriendAction("Remove friend"); await Idle();
                Check((await Peer.Friends()).Entries.Length == 0, "GUI removes both sides of friendship");
                await Peer.RequestFriend(Profile.AccountId); Click("RefreshFriendsButton"); await Idle();
                FriendAction("Accept"); await Idle();
                Check((await Peer.Friends()).Entries.Single().State == PrivateFriendState.Friends, "GUI accepts incoming request");
                await Peer.Logout();
            }
            else Check(!Control<Grid>("FriendControls").IsEnabled && Control<TextBlock>("FriendsHint").Text!.Contains("needs an update"),
                "older server explains unavailable friends instead of hiding interface");

            // Sign-in is unavailable until explicit sign-out.
            Check(!Control<ScrollViewer>("LoginSection").IsVisible, "authenticated view hides sign-in");
            Click("SignOutButton"); await Idle(); await Task.Delay(150); await Idle();
            Check(Control<ScrollViewer>("LoginSection").IsVisible, "sign-out restores sign-in");
            Check(!Control<TabItem>("FriendsTab").IsVisible && Control<StackPanel>("FriendsList").Children.Count == 0, "sign-out clears private friend data");
            Fill("LoginUsername", Username); Fill("LoginPassword", "wrong-password"); Click("SignInButton"); await Idle();
            Check(!Control<ScrollViewer>("ProfileSection").IsVisible, "failed account switch clears prior authenticated state");
            Fill("LoginPassword", Password); Click("SignInButton"); await Idle();
            Check(Control<ScrollViewer>("ProfileSection").IsVisible, "correct credentials recover sign-in");
            Check(Control<TextBox>("LoginPassword").Text == "", "login password cleared");
            Fill("ProfileGamertag", "Desktop Updated");
            // Named controls for profile actions are deliberately shared with real event paths.
            Click("SaveProfileButton"); await Idle();
            Check((await Verify.Profile()).Gamertag == "Desktop Updated", "GUI edits persist to server");
            Check(Store.Accounts(Connection.Id).Single().Gamertag == "Desktop Updated", "saved account metadata follows server update");
            Click("SignOutButton"); await Idle(); await Task.Delay(100); await Idle();
            Check(!Control<ScrollViewer>("ProfileSection").IsVisible, "sign out clears account view");
            Check(new ConnectionStore(StorePath).Accounts(Connection.Id).Single().Username == Username, "bookmarks survive reopen");

            Window.Close(); await Task.Delay(100);
            await Verify.Logout();
            return true;
        }, CancellationToken.None);
        Console.WriteLine($"PASS: {Checks} desktop checks through the real HTTPS service. Artifacts: {DirectoryName}");
    }
}
