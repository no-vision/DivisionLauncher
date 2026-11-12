namespace Division.Core.Launcher;

public sealed record AccountRegistration(string Username, string Password, string? Gamertag = null,
    string? Name = null, string? Email = null);
public sealed record AccountProfile(Guid AccountId, Guid RealmId, string Username, string Gamertag,
    string? Name, string? Email, bool EmailVerified, string? PictureSha256)
{
    public AccountIdentity Identity() => new(AccountId, RealmId, Username);
}
public sealed record ProfileUpdate(string Gamertag, string? Name, string? Email);
public sealed record LauncherLogin(string Username, string Password);
public sealed record LauncherSession(string Token, DateTimeOffset ExpiresAt, AccountProfile Profile);
public sealed record ServerDescriptor(Guid RealmId, string Name, int ProtocolVersion, string GameBuild,
    bool RegistrationEnabled, bool RecoveryDeliveryEnabled, bool InGamePictureSupported,
    GameEndpoint[]? Endpoints = null, string? GameExecutableSha256 = null, string? MinimumLauncherVersion = null,
    bool PrivateFriendsSupported = false);
public sealed record GameEndpoint(string Role, string Host, int Port);
public sealed record GameHandoff(Guid RealmId, Guid AccountId, string Gamertag, string Grant,
    DateTimeOffset ExpiresAt, string CertificatePem, GameEndpoint[] Endpoints);
public sealed record PasswordRecovery(string UsernameOrEmail);
public sealed record PasswordReset(string Token, string Password);
public sealed record ProfilePicture(byte[] Bytes, string Sha256);
public sealed record LauncherCredentials(string Session);
public sealed record GameGrantExchange(string Grant);
