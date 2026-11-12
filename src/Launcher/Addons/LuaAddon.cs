using Division.Core.Observations;
using NLua;

namespace Division.Launcher.Addons;

/// <summary>Local, user-installed observation addons. No game mutation API, CLR, filesystem,
/// network or server credentials are exposed. The instruction budget is not a memory sandbox:
/// only trusted local scripts should be installed. Lua runs outside the game process.</summary>
public sealed class LuaAddon : IDisposable
{
    private readonly Lua Runtime = new();
    private readonly LuaFunction Invoke;
    private readonly LuaFunction NewTable;
    public string Name { get; }
    public string? Error { get; private set; }

    public LuaAddon(string Name, string Source)
    {
        this.Name = Name;
        try
        {
            object[] Helpers = Runtime.DoString("""
                local function bounded(fn, ...)
                    local used = 0
                    debug.sethook(function()
                        used = used + 1000
                        if used > 100000 then error('addon_instruction_budget') end
                    end, '', 1000)
                    local result = table.pack(pcall(fn, ...))
                    debug.sethook()
                    if not result[1] then error(result[2]) end
                    return table.unpack(result, 2, result.n)
                end
                return function(source)
                    local env = {assert=assert, error=error, pairs=pairs, ipairs=ipairs,
                        type=type, tostring=tostring, tonumber=tonumber,
                        math={min=math.min,max=math.max,floor=math.floor,abs=math.abs},
                        string={find=string.find,sub=string.sub,len=string.len,format=string.format}}
                    local addon = bounded(assert(load(source, 'addon', 't', env)))
                    assert(type(addon.OnEvent) == 'function', 'missing_OnEvent')
                    return function(event) return bounded(addon.OnEvent, event) end
                end, function() return {} end
                """);
            using LuaFunction Compile = (LuaFunction)Helpers[0];
            NewTable = (LuaFunction)Helpers[1];
            Invoke = (LuaFunction)Compile.Call(Source)[0];
        }
        catch { Runtime.Dispose(); throw; }
    }

    public string? Observe(MessageObservation Event)
    {
        if (Error is not null) return null;
        try
        {
            using LuaTable Input = (LuaTable)NewTable.Call()[0];
            Input["Kind"] = Event.Kind; Input["Source"] = Event.Source;
            Input["Direction"] = Event.Direction; Input["Service"] = Event.Service;
            Input["MessageId"] = Event.MessageId is uint Id ? (long)Id : null;
            Input["PayloadBytes"] = Event.PayloadBytes; Input["IdBytes"] = Event.IdBytes;
            Input["Hex"] = Convert.ToHexString(Event.Message);
            Input["Status"] = Event.Status; Input["Detail"] = Event.Detail;
            Input["Sequence"] = Event.Sequence; Input["Connection"] = Event.Connection.ToString("N");
            Input["Redacted"] = Event.Redacted; Input["Truncated"] = Event.Truncated;
            object[] Result = Invoke.Call(Input);
            string? Text = Result.Length == 0 ? null : Result[0] as string;
            return Text is null ? null : Text[..Math.Min(Text.Length, 2048)];
        }
        catch (Exception Failure) when (Failure is NLua.Exceptions.LuaException or InvalidCastException)
        {
            Error = "Addon disabled: " + Failure.Message[..Math.Min(Failure.Message.Length, 250)];
            return Error;
        }
    }

    public void Dispose() { Invoke.Dispose(); NewTable.Dispose(); Runtime.Dispose(); }
}
