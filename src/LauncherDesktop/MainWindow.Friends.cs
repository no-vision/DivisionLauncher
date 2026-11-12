using Avalonia;
using Avalonia.Controls;
using Avalonia.Interactivity;
using Division.Core.Launcher;

namespace Division.Launcher.Desktop;

public sealed partial class MainWindow
{
    private string? NextFriendPage;

    private void ClearFriends()
    {
        FriendsTab.IsVisible = false;
        FriendsList.Children.Clear(); FriendUsername.Text = "";
        NextFriendPage = null; FriendsNext.IsEnabled = false;
        FriendsHint.Text = "Refresh to load friends and pending requests.";
    }

    private async Task LoadFriends(string? After = null)
    {
        PrivateFriendsPage Page = await RequireClient().Friends(After);
        FriendsList.Children.Clear();
        foreach (PrivateFriend Friend in Page.Entries) ShowFriend(Friend);
        NextFriendPage = Page.Next; FriendsNext.IsEnabled = Page.Next is not null;
        FriendsHint.Text = Page.Entries.Length == 0 ? "No friends or pending requests on this page." :
            $"{Page.Entries.Length} friends and requests. Refresh to check for changes.";
    }

    private async void RefreshFriends(object? Sender, RoutedEventArgs Event) =>
        await Execute("Loading friends…", async () => { await LoadFriends(); Status("Friends updated."); });

    private async void NextFriends(object? Sender, RoutedEventArgs Event) =>
        await Execute("Loading next page…", async () => { await LoadFriends(NextFriendPage); Status("Friends updated."); });

    private async void FindFriend(object? Sender, RoutedEventArgs Event) =>
        await Execute("Finding player…", async () =>
        {
            PrivateFriend Friend = await RequireClient().FindFriend(FriendUsername.Text?.Trim() ?? "");
            FriendsList.Children.Clear(); ShowFriend(Friend);
            NextFriendPage = null; FriendsNext.IsEnabled = false;
            FriendsHint.Text = "Player found. Refresh to return to your friends and requests.";
            Status("Found " + Friend.Username + ".");
        });

    private void ShowFriend(PrivateFriend Friend)
    {
        StackPanel Row = new() { Spacing = 6, Margin = new Thickness(0, 4) };
        Row.Children.Add(new TextBlock { Text = Friend.Username, FontSize = 17 });
        Row.Children.Add(new TextBlock { Text = Friend.State switch
        {
            PrivateFriendState.Friends => Friend.Online ? "Friends — in game" : "Friends — offline",
            PrivateFriendState.Incoming => "Wants to be your friend",
            PrivateFriendState.Outgoing => "Request sent — awaiting acceptance",
            PrivateFriendState.MutualPending => "You both sent requests — accept to confirm",
            _ => "Not yet friends"
        }});
        StackPanel Actions = new() { Orientation = Avalonia.Layout.Orientation.Horizontal, Spacing = 8 };
        void Action(string Label, Func<Task<PrivateFriend>> Run)
        {
            Button Button = new() { Content = Label };
            Button.Click += async (_, _) => await Execute(Label + "…", async () =>
            { await Run(); await LoadFriends(); Status("Friendship updated for " + Friend.Username + "."); });
            Actions.Children.Add(Button);
        }
        if (Friend.State == PrivateFriendState.None)
            Action("Send friend request", () => RequireClient().RequestFriend(Friend.AccountId));
        if (Friend.State is PrivateFriendState.Incoming or PrivateFriendState.MutualPending)
            Action("Accept", () => RequireClient().AcceptFriend(Friend.AccountId));
        if (Friend.State != PrivateFriendState.None)
            Action(Friend.State switch { PrivateFriendState.Friends => "Remove friend", PrivateFriendState.Outgoing => "Cancel request", _ => "Decline" },
                () => RequireClient().RemoveFriend(Friend.AccountId));
        if (Friend.State == PrivateFriendState.Friends && Friend.Online)
        {
            Button Invite = new() { Content = "Invite to group" };
            Invite.Click += async (_, _) => await Execute("Sending in-game invitation…", async () =>
            {
                await RequireClient().InviteFriend(Friend.AccountId);
                Status("Invitation requested. Accept the timed invitation in the other player's game.");
            });
            Actions.Children.Add(Invite);
        }
        Row.Children.Add(Actions); FriendsList.Children.Add(Row);
    }
}
