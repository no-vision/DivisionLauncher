using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using Division.Core.Launcher;
using Division.Core.Observations;

namespace Division.Launcher;

/// <summary>One server origin per client. Redirects cannot forward credentials to a different host.</summary>
public sealed class LauncherSessionExpiredException() : InvalidOperationException("Your sign-in session expired or was revoked. Please sign in again.");

public sealed class ServerClient : IDisposable
{
    private readonly HttpClient Client;
    private readonly Guid? ExpectedRealm;
    private Guid? VerifiedRealm;
    private LauncherSession? Session;
    public Uri Address => Client.BaseAddress!;
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public static string NormalizeAddress(string Address)
        => ValidateUrl(Address.Trim().Contains("://", StringComparison.Ordinal) ? Address.Trim() : "https://" + Address.Trim()).ToString();

    public static Uri ValidateUrl(string Value)
    {
        Uri Address = new(Value, UriKind.Absolute);
        bool Loopback = Address.Host == "localhost" || IPAddress.TryParse(Address.Host,out IPAddress? Ip) && IPAddress.IsLoopback(Ip);
        if (Address.UserInfo.Length != 0 || Address.Query.Length != 0 || Address.Fragment.Length != 0 ||
            Address.AbsolutePath != "/" || Address.Scheme != "https" && !(Address.Scheme == "http" && Loopback))
            throw new ArgumentException("Use an HTTPS server origin, or HTTP on localhost for development.");
        return Address;
    }

    public ServerClient(string Address, string? Pin = null, Guid? Realm = null)
    {
        HttpClientHandler Handler = new() { AllowAutoRedirect=false, UseCookies=false };
        if (Pin is not null)
        {
            byte[] Expected = Convert.FromHexString(Pin.Replace(":",""));
            if (Expected.Length != 32) throw new ArgumentException("Certificate pin must be a SHA256 fingerprint supplied by the server operator.");
            Handler.ServerCertificateCustomValidationCallback = (_, Certificate, _, _) => Certificate is not null &&
                CryptographicOperations.FixedTimeEquals(SHA256.HashData(Certificate.RawData), Expected) &&
                DateTime.UtcNow >= Certificate.NotBefore.ToUniversalTime() && DateTime.UtcNow < Certificate.NotAfter.ToUniversalTime();
        }
        Client = new(Handler) { BaseAddress=ValidateUrl(Address), Timeout=TimeSpan.FromSeconds(20), MaxResponseContentBufferSize=2*1024*1024 };
        ExpectedRealm = Realm;
    }

    public async Task<ServerDescriptor> Verify()
    {
        ServerDescriptor Server = await Send<ServerDescriptor>(HttpMethod.Get,"/launcher/server",null,false);
        if (Server.ProtocolVersion != 1 || Server.RealmId == Guid.Empty || ExpectedRealm is Guid Expected && Server.RealmId != Expected)
            throw new InvalidOperationException("Server identity or launcher protocol changed. No credentials were sent.");
        VerifiedRealm = Server.RealmId; return Server;
    }

    public async Task<EndpointCheck[]> CheckConnection()
    {
        ServerDescriptor Descriptor = await Verify();
        ValidateCompatibility(Descriptor);
        if (Descriptor.Endpoints is null)
            throw new InvalidOperationException("This server does not publish game endpoint checks yet. Update its authentication service; Play will still check the login handoff.");
        return await ConnectionPreflight.Check(Address, Descriptor.Endpoints);
    }

    public static void ValidateCompatibility(ServerDescriptor Descriptor)
    {
        if (Descriptor.MinimumLauncherVersion is string Minimum &&
            (!Version.TryParse(Minimum, out Version? Required) || new Version(LauncherRelease.CurrentVersion) < Required))
            throw new InvalidOperationException("This server requires launcher " + Minimum + ". Install an update from your trusted launcher distributor.");
        if (Descriptor.GameExecutableSha256 is string Hash &&
            !Hash.Equals(LauncherCompatibility.ExecutableSha256, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("This server requires a game build that this launch adapter does not support.");
    }

    private async Task<T> Send<T>(HttpMethod Method, string Path, object? Body, bool Authorized)
    {
        using HttpRequestMessage Request = new(Method,Path);
        if (Body is not null) Request.Content = JsonContent.Create(Body,options:Json);
        if (Authorized && (Session is null || Session.ExpiresAt <= DateTimeOffset.UtcNow))
        { Session = null; throw new LauncherSessionExpiredException(); }
        if (Authorized) Request.Headers.Authorization = new AuthenticationHeaderValue("Bearer",Session?.Token ?? throw new InvalidOperationException("Log in first."));
        using HttpResponseMessage Response = await Client.SendAsync(Request);
        try { return await Read<T>(Response); }
        catch (LauncherSessionExpiredException) { Session = null; throw; }
    }

    private static async Task<T> Read<T>(HttpResponseMessage Response)
    {
        if (!Response.IsSuccessStatusCode)
        {
            string Detail = await Response.Content.ReadAsStringAsync();
            if (Response.StatusCode == HttpStatusCode.Unauthorized ||
                Detail.Contains("launcher_session_expired", StringComparison.Ordinal) || Detail.Contains("launcher_session_required", StringComparison.Ordinal))
                throw new LauncherSessionExpiredException();
            if (Response.StatusCode == HttpStatusCode.ServiceUnavailable)
                throw new InvalidOperationException("This server is temporarily unavailable or under maintenance. Try again later.");
            throw new InvalidOperationException($"Server returned {(int)Response.StatusCode}: {Detail[..Math.Min(Detail.Length,300)]}");
        }
        if (typeof(T)==typeof(bool)) return (T)(object)true;
        return await Response.Content.ReadFromJsonAsync<T>(Json) ?? throw new InvalidOperationException("Empty server response.");
    }

    public async Task<AccountProfile> Register(AccountRegistration Registration)
    {
        await Verify(); AccountProfile Profile = await Send<AccountProfile>(HttpMethod.Post,"/launcher/accounts",Registration,false);
        CheckProfile(Profile); return Profile;
    }

    public async Task<AccountProfile> Login(LauncherLogin Login)
    {
        await Verify(); LauncherSession Reply = await Send<LauncherSession>(HttpMethod.Post,"/launcher/login",Login,false);
        CheckProfile(Reply.Profile); Session=Reply; return Reply.Profile;
    }

    private void CheckProfile(AccountProfile Profile)
    { if (Profile.RealmId != VerifiedRealm || Profile.AccountId==Guid.Empty) throw new InvalidOperationException("Foreign server account response."); }

    public Task<AccountProfile> Profile() => Send<AccountProfile>(HttpMethod.Get,"/launcher/profile",null,true);
    public Task<PrivateFriendsPage> Friends(string? After = null) => Send<PrivateFriendsPage>(HttpMethod.Get,
        "/launcher/friends" + (After is null ? "" : "?after=" + Uri.EscapeDataString(After)), null, true);
    public Task<PrivateFriend> FindFriend(string Username) => Send<PrivateFriend>(HttpMethod.Post,
        "/launcher/friends/lookup", new PrivateFriendLookup(Username), true);
    public Task<PrivateFriend> RequestFriend(Guid Account) => Send<PrivateFriend>(HttpMethod.Post,
        "/launcher/friends/request", new PrivateFriendTarget(Account), true);
    public Task<PrivateFriend> AcceptFriend(Guid Account) => Send<PrivateFriend>(HttpMethod.Post,
        "/launcher/friends/accept", new PrivateFriendTarget(Account), true);
    public Task<PrivateFriend> InviteFriend(Guid Account) => Send<PrivateFriend>(HttpMethod.Post,
        "/launcher/friends/invite", new PrivateFriendTarget(Account), true);
    public Task<PrivateFriend> RemoveFriend(Guid Account) => Send<PrivateFriend>(HttpMethod.Post,
        "/launcher/friends/remove", new PrivateFriendTarget(Account), true);
    public Task<ObservationBatch> Observations(ObservationQuery Query) => Send<ObservationBatch>(HttpMethod.Post,"/launcher/observations",Query,true);
    public Task<AccountProfile> Update(ProfileUpdate Update) => Send<AccountProfile>(HttpMethod.Post,"/launcher/profile",Update,true);
    public async Task<AccountProfile> Picture(string FileName)
    {
        if (new FileInfo(FileName).Length > 1048576) throw new ArgumentException("Picture must be a PNG of at most 1 MiB.");
        using HttpRequestMessage Request = new(HttpMethod.Post,"/launcher/picture");
        Request.Headers.Authorization=new("Bearer",Session?.Token??throw new InvalidOperationException("Log in first."));
        Request.Content=new ByteArrayContent(await File.ReadAllBytesAsync(FileName)); Request.Content.Headers.ContentType=new("image/png");
        using HttpResponseMessage Response=await Client.SendAsync(Request); return await Read<AccountProfile>(Response);
    }
    public async Task<byte[]?> PictureBytes(Guid Account)
    {
        using HttpResponseMessage Response = await Client.GetAsync("/launcher/picture/" + Account.ToString("D"));
        if (Response.StatusCode == HttpStatusCode.NotFound) return null;
        Response.EnsureSuccessStatusCode();
        return await Response.Content.ReadAsByteArrayAsync();
    }
    public async Task<GameHandoff> Handoff()
    {
        GameHandoff Reply = await Send<GameHandoff>(HttpMethod.Post,"/launcher/launch",null,true);
        if (Reply.RealmId != VerifiedRealm || Reply.AccountId != Session!.Profile.AccountId || Reply.ExpiresAt <= DateTimeOffset.UtcNow)
            throw new InvalidOperationException("Invalid game handoff identity or expiration.");
        ConnectionPreflight.ValidateEndpoints(Reply.Endpoints);
        return Reply;
    }
    public async Task Recovery(string Identity) { await Verify(); await Send<bool>(HttpMethod.Post,"/launcher/recovery",new PasswordRecovery(Identity),false); }
    public async Task Reset(PasswordReset Reset) { await Verify(); await Send<bool>(HttpMethod.Post,"/launcher/reset-password",Reset,false); }
    public async Task Logout() { if(Session is not null) await Send<bool>(HttpMethod.Post,"/launcher/logout",null,true); Session=null; }
    public void Dispose() => Client.Dispose();
}
