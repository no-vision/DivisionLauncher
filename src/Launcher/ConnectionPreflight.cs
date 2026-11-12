using System.Net;
using System.Net.Sockets;
using Division.Core.Launcher;

namespace Division.Launcher;

public sealed record EndpointCheck(string Role, string Host, int Port, bool Reachable, string Detail);

/// <summary>Bounded, credential-free TCP reachability checks. A successful connect
/// proves the port is reachable, not native game authentication or world readiness.</summary>
public static class ConnectionPreflight
{
    public static void ValidateEndpoints(GameEndpoint[] Endpoints)
    {
        if (Endpoints.Length != 4 || !Endpoints.Select(Endpoint => Endpoint.Role).Order()
            .SequenceEqual(new[] { "lobby", "ping", "proxy", "world" }) || Endpoints.Any(Endpoint =>
                Endpoint.Port is < 1 or > 65535 || string.IsNullOrWhiteSpace(Endpoint.Host) ||
                Uri.CheckHostName(Endpoint.Host) == UriHostNameType.Unknown))
            throw new InvalidOperationException("The server advertised invalid game endpoints. Contact its administrator.");
    }

    public static async Task<EndpointCheck[]> Check(Uri Server, GameEndpoint[] Endpoints, CancellationToken Cancellation = default)
    {
        ValidateEndpoints(Endpoints);
        return await Task.WhenAll(Endpoints.Select(Probe));

        async Task<EndpointCheck> Probe(GameEndpoint Endpoint)
        {
            using CancellationTokenSource Timeout = CancellationTokenSource.CreateLinkedTokenSource(Cancellation);
            Timeout.CancelAfter(TimeSpan.FromSeconds(5));
            try
            {
                IPAddress[] Addresses = await Dns.GetHostAddressesAsync(Endpoint.Host, Timeout.Token);
                if (Addresses.Length == 0) return new(Endpoint.Role, Endpoint.Host, Endpoint.Port, false, "Hostname has no addresses.");
                // Private LAN/VPN addresses are valid. Loopback and wildcard addresses
                // advertised by a remote server would point at the player's machine.
                if (Addresses.Any(Address => Address.Equals(IPAddress.Any) || Address.Equals(IPAddress.IPv6Any)) ||
                    (!Server.IsLoopback && Addresses.Any(IPAddress.IsLoopback)))
                    return new(Endpoint.Role, Endpoint.Host, Endpoint.Port, false, "Server advertises a local-only address. Its administrator must set the public game host.");
                using TcpClient Client = new();
                await Client.ConnectAsync(Addresses, Endpoint.Port, Timeout.Token);
                return new(Endpoint.Role, Endpoint.Host, Endpoint.Port, true, "TCP reachable");
            }
            catch (OperationCanceledException) when (!Cancellation.IsCancellationRequested)
            { return new(Endpoint.Role, Endpoint.Host, Endpoint.Port, false, "Timed out. Check the server firewall and game ports."); }
            catch (SocketException Error)
            { return new(Endpoint.Role, Endpoint.Host, Endpoint.Port, false, "Connection failed: " + Error.SocketErrorCode); }
        }
    }

    public static void RequireReachable(EndpointCheck[] Checks)
    {
        EndpointCheck[] Failed = Checks.Where(Check => !Check.Reachable).ToArray();
        if (Failed.Length != 0)
            throw new InvalidOperationException("Game connection check failed:\n" + string.Join("\n", Failed.Select(Check =>
                $"{Check.Role} — {Check.Host}:{Check.Port}: {Check.Detail}")));
    }
}
