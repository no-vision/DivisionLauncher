using System.Net;
using System.Net.Sockets;
using System.Text.Json;
using Division.Core.Launcher;

namespace Division.Launcher.Desktop.Tests;

internal static class RemoteLauncherChecks
{
    public static async Task Run()
    {
        int Count = 0;
        void Check(bool Value, string Message) { if (!Value) throw new InvalidOperationException(Message); Count++; }
        void Reject(Action Action, string Message)
        {
            bool Failed = false;
            try { Action(); } catch (InvalidOperationException) { Failed = true; }
            Check(Failed, Message);
        }
        using TcpListener Listener = new(IPAddress.Loopback, 0); Listener.Start();
        int Port = ((IPEndPoint)Listener.LocalEndpoint).Port;
        GameEndpoint[] Endpoints = new[] { "proxy", "lobby", "world", "ping" }.Select(Role => new GameEndpoint(Role, "127.0.0.1", Port)).ToArray();
        Uri Local = new("http://127.0.0.1");
        Check((await ConnectionPreflight.Check(Local, Endpoints)).All(Result => Result.Reachable), "all four reachable roles pass");
        Check((await ConnectionPreflight.Check(new("https://community.example"), Endpoints)).All(Result => !Result.Reachable), "remote advertised loopback cannot connect to player services");
        Reject(() => ConnectionPreflight.ValidateEndpoints([Endpoints[0], Endpoints[0], Endpoints[2], Endpoints[3]]), "duplicate roles rejected");
        Reject(() => ConnectionPreflight.ValidateEndpoints(Endpoints.Select(Value => Value with { Port = 0 }).ToArray()), "invalid port rejected");
        Listener.Stop();
        EndpointCheck[] Closed = await ConnectionPreflight.Check(Local, Endpoints);
        Reject(() => ConnectionPreflight.RequireReachable(Closed), "closed game ports block launch");
        ServerDescriptor Descriptor = new(Guid.NewGuid(), "Fixture", 1, LauncherCompatibility.GameBuild, true, false, false,
            Endpoints, LauncherCompatibility.ExecutableSha256, "0.3.0");
        ServerClient.ValidateCompatibility(Descriptor);
        Reject(() => ServerClient.ValidateCompatibility(Descriptor with { MinimumLauncherVersion = "999.0.0" }), "newer required launcher blocks play");
        Reject(() => ServerClient.ValidateCompatibility(Descriptor with { GameExecutableSha256 = new string('0', 64) }), "unsupported executable blocks play");

        // A real HTTP exchange checks the existing server's409 session-expiration
        // convention and confirms the invalidated token is not reused afterward.
        using TcpListener Reserve = new(IPAddress.Loopback, 0); Reserve.Start();
        int ApiPort = ((IPEndPoint)Reserve.LocalEndpoint).Port; Reserve.Stop();
        string Url = $"http://127.0.0.1:{ApiPort}/";
        using HttpListener Api = new(); Api.Prefixes.Add(Url); Api.Start();
        AccountProfile Profile = new(Guid.NewGuid(), Descriptor.RealmId, "fixture", "Fixture", null, null, false, null);
        int ProfileRequests = 0;
        Task Serving = Task.Run(async () =>
        {
            for (int Index = 0; Index < 3; Index++)
            {
                HttpListenerContext Context = await Api.GetContextAsync();
                object Body;
                if (Context.Request.Url!.AbsolutePath == "/launcher/server") Body = Descriptor;
                else if (Context.Request.Url.AbsolutePath == "/launcher/login") Body = new LauncherSession("fixture-token", DateTimeOffset.UtcNow.AddMinutes(1), Profile);
                else { ProfileRequests++; Context.Response.StatusCode = 409; Body = new { error = "launcher_session_expired" }; }
                Context.Response.ContentType = "application/json";
                await JsonSerializer.SerializeAsync(Context.Response.OutputStream, Body, Body.GetType(), new JsonSerializerOptions(JsonSerializerDefaults.Web));
                Context.Response.Close();
            }
        });
        using ServerClient Client = new(Url, Realm: Descriptor.RealmId);
        await Client.Login(new("fixture", "fixture-password"));
        for (int Attempt = 0; Attempt < 2; Attempt++)
        {
            bool Expired = false;
            try { await Client.Profile(); } catch (LauncherSessionExpiredException) { Expired = true; }
            Check(Expired, "expired login requires reauthentication");
        }
        await Serving.WaitAsync(TimeSpan.FromSeconds(5));
        Check(ProfileRequests == 1, "revoked token is cleared before another request");
        Console.WriteLine($"PASS: {Count} remote launcher authority/connectivity checks.");
    }
}
