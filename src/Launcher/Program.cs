using System.Diagnostics;
using System.Text;
using System.Text.Json;
using Division.Core.Launcher;
using Division.Launcher;
using Division.Launcher.Addons;

// The shell wrapper supplies the installation root. Server metadata never supplies executable paths or arguments.
string[] Positional = args.Where(Value => !Value.StartsWith("--", StringComparison.Ordinal)).ToArray();
string Root = Path.GetFullPath(Positional.Length>0?Positional[0]:".");
ConnectionStore Store = new(Positional.Length>1?Path.GetFullPath(Positional[1]):Path.Combine(LauncherPaths.StateRoot(Root),"connections.sqlite3"));
bool AddonsEnabled = args.Contains("--addons");
string Ask(string Prompt, string? Default=null)
{
    Console.Write(Prompt+(Default is null?": ":$" [{Default}]: "));
    string Value=Console.ReadLine()??throw new OperationCanceledException();
    return string.IsNullOrWhiteSpace(Value)?Default??"":Value.Trim();
}
string Secret(string Prompt)
{
    Console.Write(Prompt+": ");
    if(Console.IsInputRedirected) return Console.ReadLine()??throw new OperationCanceledException();
    StringBuilder Value=new();
    while(true)
    {
        ConsoleKeyInfo Key=Console.ReadKey(true);
        if(Key.Key==ConsoleKey.Enter) { Console.WriteLine(); return Value.ToString(); }
        if(Key.Key==ConsoleKey.Backspace) { if(Value.Length>0) Value.Length--; }
        else if(!char.IsControl(Key.KeyChar)) Value.Append(Key.KeyChar);
    }
}
GameInstallation Configure()
{
    GameInstallation? Previous=Store.Installation();
    GameInstallation Installation=new(Ask("Game directory",Previous?.GameDirectory??""),
        OperatingSystem.IsWindows() ? "" : Ask("Proton directory",Previous?.ProtonDirectory??""),
        OperatingSystem.IsWindows() ? "" : Ask("Frida runtime directory",Previous?.FridaDirectory??LauncherPaths.AdapterRuntime(Root)));
    GameLauncher.ValidateInstallation(Installation);
    Store.SetInstallation(Installation); return Installation;
}
async Task Launch(ServerClient Server)
{
    GameInstallation Installation=Store.Installation()??Configure();
    string[] Scripts = AddonsEnabled ? new AddonCatalog(Root, Path.Combine(LauncherPaths.StateRoot(Root), "profiles/addons.sqlite3"))
        .Discover().Where(Entry => Entry.ScriptPath is not null).Select(Entry => Entry.Id).ToArray() : [];
    int ExitCode = await new GameLauncher(Root).Run(Server, Installation,
        new AddonSelection(Scripts, AddonsEnabled, AddonsEnabled), Console.WriteLine);
    if (ExitCode != 0) Console.WriteLine($"Client adapter exited with code {ExitCode}; inspect the reported run directory.");
}
async Task AccountMenu(ServerConnection Connection)
{
    using ServerClient Server=new(Connection.ApiUrl,Connection.CertificateSha256,Connection.RealmId);
    ServerDescriptor Descriptor=await Server.Verify();
    Console.WriteLine($"{Descriptor.Name} — {Connection.ApiUrl}");
    string Action=Ask("[L]ogin, [C]reate account, [R]ecover, reset [P]assword, [B]ack","L").ToUpperInvariant();
    if(Action=="B")return;
    if(Action=="R") { await Server.Recovery(Ask("Username or recovery email")); Console.WriteLine("If an account matches, recovery instructions will be sent."); return; }
    if(Action=="P") { await Server.Reset(new(Secret("Recovery code"),Secret("New password"))); Console.WriteLine("Password changed."); return; }
    string Username=Ask("Username",Connection.LastUsername);
    string Password=Secret(Action=="C"?"Choose password (8 or more characters)":"Password");
    if(Action=="C")
    {
        await Server.Register(new(Username,Password,Ask("Gamertag",Username),Ask("Name (optional)"),Ask("Recovery email (optional)")));
        Console.WriteLine("Account created on this server.");
    }
    else if(Action!="L")return;
    AccountProfile Profile=await Server.Login(new(Username,Password)); Password="";
    Store.Save(Connection with { LastUsername=Profile.Username });
    Console.WriteLine($"Logged in as {Profile.Gamertag}.");
    try
    {
        await Launch(Server);
        while(true)
        {
            string Choice=Ask("[P]lay again, [E]dit profile, profile [I]mage, [L]ogout","L").ToUpperInvariant();
            if(Choice=="L")break;
            if(Choice=="P")await Launch(Server);
            if(Choice=="E")
            {
                Profile=await Server.Profile();
                Profile=await Server.Update(new(Ask("Gamertag",Profile.Gamertag),Ask("Name",Profile.Name),Ask("Email",Profile.Email)));
                Console.WriteLine("Profile saved.");
            }
            if(Choice=="I")
            {
                Profile=await Server.Picture(Ask("PNG file (up to 1024x1024 and 1 MiB)"));
                Console.WriteLine(Descriptor.InGamePictureSupported?"Profile picture saved.":"Profile picture saved. This client adapter does not yet display custom pictures in-game.");
            }
        }
    }
    finally { await Server.Logout(); }
}

Console.WriteLine("The Division Private — connection manager");
while(true)
{
    try
    {
        ServerConnection[] Servers=Store.List();
        for(int Index=0;Index<Servers.Length;Index++)Console.WriteLine($"{Index+1}. {Servers[Index].Label} ({Servers[Index].ApiUrl})");
        string Choice=Ask($"Server number, [A]dd server, [D]elete server, [I]nstallation, [T]ools ({(AddonsEnabled ? "on" : "off")}), [Q]uit").ToUpperInvariant();
        if(Choice=="Q")break;
        if(Choice=="T") { AddonsEnabled = !AddonsEnabled; continue; }
        if(Choice=="I") { Configure(); continue; }
        if(Choice=="A")
        {
            string Label=Ask("Server label"), Address=ServerClient.ValidateUrl(Ask("Server URL")).ToString();
            string Pin=Ask("Certificate SHA256 fingerprint (optional, for a server using its own certificate)");
            using ServerClient Server=new(Address,Pin.Length==0?null:Pin);
            ServerDescriptor Descriptor=await Server.Verify();
            Store.Save(new(Guid.NewGuid(),Label,Address,Descriptor.RealmId,Pin.Length==0?null:Pin,null));
            Console.WriteLine($"Saved {Descriptor.Name}; server identity {Descriptor.RealmId}."); continue;
        }
        if(Choice=="D") { int Index=int.Parse(Ask("Server number"))-1; Store.Remove(Servers[Index].Id); continue; }
        if(int.TryParse(Choice,out int Number) && Number>0 && Number<=Servers.Length) await AccountMenu(Servers[Number-1]);
    }
    catch(OperationCanceledException) { break; }
    catch(Exception Error) when(Error is ArgumentException or InvalidOperationException or IOException or HttpRequestException or JsonException or Microsoft.Data.Sqlite.SqliteException or IndexOutOfRangeException or FormatException)
    { Console.WriteLine(Error.Message); }
}
