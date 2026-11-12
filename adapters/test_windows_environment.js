// Critical isolation contract; exercise the complete agent with mocked native APIs.
const Assert = require('node:assert/strict');
const Fs = require('node:fs');
const Vm = require('node:vm');
const Replacements = new Map(), Events = [];
let Caller = 'uplay_r1_loader64.dll', Forwarded = 0;
function Pointer(Value) {
  return {isNull:()=>Value===0, toUInt32:()=>Value>>>0,
    readUtf16String:()=>Value, writePointer:Value=>{ OutputValue=Value; }};
}
let OutputValue;
let SocketResult={value:-1,lastError:10035}, SocketCalls=0;
const Context = {
  PRIVATE_WINDOWS_CONFIG:{documents:'C:\\PrivateLauncher\\documents'},
  Process:{platform:'windows',arch:'x64',findModuleByAddress:()=>({name:Caller}),
    attachModuleObserver:()=>({})},
  Module:{load:()=>({getExportByName:Name=>Name})},
  NativeFunction:function(Address) { return (...Args)=>{ Forwarded++; return 123; }; },
  SystemFunction:function(Address) { return (...Args)=>{ SocketCalls++; return SocketResult; }; },
  NativeCallback:function(Callback) { return Callback; },
  Interceptor:{replace:(Address,Callback)=>Replacements.set(Address,Callback),attach:()=>({})},
  ptr:Pointer, send:Event=>Events.push(Event.event)
};
Vm.runInNewContext(Fs.readFileSync(__dirname+'/windows_environment_agent.js','utf8'),Context);
const Open = Replacements.get('RegOpenKeyExW');
function Call(Name,Root=0x80000002) {
  return Open.call({returnAddress:Pointer(1)},Pointer(Root),Pointer(Name),0,0x20219,Pointer(1));
}
Assert.equal(Call('SOFTWARE\\Ubisoft\\Launcher'),2);
Assert.equal(OutputValue.toUInt32(),0);
Assert.equal(Call('software\\ubisoft\\launcher\\'),2);
Assert.equal(Forwarded,0);
Assert.equal(Events.filter(Event=>Event==='private_platform_install_lookup_isolated').length,1);
for (const Name of ['SOFTWARE\\Ubisoft\\LauncherExtra','SOFTWARE\\Other','SOFTWARE\\Ubisoft\\Launcher\\Settings'])
  Assert.equal(Call(Name),123);
Assert.equal(Call('SOFTWARE\\Ubisoft\\Launcher',0x80000001),123);
Caller='thedivision.exe'; Assert.equal(Call('SOFTWARE\\Ubisoft\\Launcher'),123);
Caller='another.dll'; Assert.equal(Call('SOFTWARE\\Ubisoft\\Launcher'),123);
Assert.equal(Forwarded,6);
console.log('Windows registry isolation scope: passed');

function SockAddress(Port=25100,Host=[127,0,0,1]) {
  const Bytes=Buffer.alloc(16); Bytes.writeUInt16LE(2); Bytes.writeUInt16BE(Port,2); Buffer.from(Host).copy(Bytes,4);
  return {isNull:()=>false,readU16:()=>Bytes.readUInt16LE(0),add:Index=>({readU8:()=>Bytes[Index]})};
}
for(const Name of ['connect','WSAConnect']) {
  const Call=Replacements.get(Name), Invocation={lastError:0};
  SocketResult={value:-1,lastError:10035};
  Assert.equal(Call.call(Invocation,123,SockAddress(),16),-1);
  Assert.equal(Invocation.lastError,10035,'preserve WSAEWOULDBLOCK');
  SocketResult={value:0,lastError:0};
  Assert.equal(Call.call(Invocation,123,SockAddress(25500),16),0);
  Assert.equal(Invocation.lastError,0);
  const Before=SocketCalls;
  Assert.equal(Call.call(Invocation,123,SockAddress(443,[8,8,8,8]),16),-1);
  Assert.equal(Invocation.lastError,10013,'deny other destinations');
  Assert.equal(SocketCalls,Before,'denied calls never reach original API');
}
SocketResult={value:-1,lastError:10035};
const SendContext={lastError:0};
Assert.equal(Replacements.get('sendto').call(SendContext,123,Pointer(1),10,0,Pointer(0),0),-1);
Assert.equal(SendContext.lastError,10035,'connected send error is preserved too');
console.log('Windows socket result/error preservation and destination isolation: passed');
