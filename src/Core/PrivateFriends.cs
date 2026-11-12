namespace Division.Core.Launcher;

public enum PrivateFriendState { None, Outgoing, Incoming, MutualPending, Friends }
public sealed record PrivateFriend(Guid AccountId, string Username, PrivateFriendState State, bool Online = false);
public sealed record PrivateFriendsPage(PrivateFriend[] Entries, string? Next);
public sealed record PrivateFriendLookup(string Username);
public sealed record PrivateFriendTarget(Guid AccountId);
