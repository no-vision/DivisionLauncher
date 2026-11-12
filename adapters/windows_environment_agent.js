'use strict';
// Native Windows private-process boundary. No machine-wide hosts/firewall edits.
// Only the newly suspended private game is instrumented. Unlike the Linux network
// namespace, this is API-level routing/isolation, not an OS security sandbox.
(() => {
  const Config = globalThis.PRIVATE_WINDOWS_CONFIG;
  if (Process.platform !== 'windows' || Process.arch !== 'x64' || !Config ||
      !/^[A-Za-z]:\\/.test(Config.documents) || Config.documents.includes('"') ||
      Config.documents.length > 220) throw new Error('Invalid private Windows directory');
  const Hooks = [], Callbacks = [];
  // Match the fresh Proton prefix for the loader's installation discovery only.
  // This does not modify the machine registry, SDK results or ownership state.
  const Registry = Module.load('advapi32.dll');
  const OpenKeyAddress = Registry.getExportByName('RegOpenKeyExW');
  const OpenKey = new NativeFunction(OpenKeyAddress,'int',['pointer','pointer','uint','uint','pointer']);
  let RegistryReported = false;
  const PrivateOpenKey = new NativeCallback(function(Root, SubKey, Options, Access, Output) {
    const Caller = Process.findModuleByAddress(this.returnAddress);
    if (Caller && /^uplay_r1_loader64\.dll$/i.test(Caller.name) &&
        Root.toUInt32()===0x80000002 && !SubKey.isNull() &&
        /^software\\ubisoft\\launcher\\?$/i.test(SubKey.readUtf16String())) {
      if (!Output.isNull()) Output.writePointer(ptr(0));
      if (!RegistryReported) {
        RegistryReported=true; send({event:'private_platform_install_lookup_isolated'});
      }
      return 2; // ERROR_FILE_NOT_FOUND, as in the clean private Proton prefix.
    }
    return OpenKey(Root,SubKey,Options,Access,Output);
  },'int',['pointer','pointer','uint','uint','pointer']);
  Callbacks.push(PrivateOpenKey); Interceptor.replace(OpenKeyAddress,PrivateOpenKey);
  send({event:'private_platform_registry_isolated'});
  // Observe the copied Steam distribution's own handoff. Never change platform
  // authentication, SDK return values or another process.
  let SteamObserved = false;
  const PlatformObserver = Process.attachModuleObserver({onAdded(ModuleInfo) {
    if (SteamObserved || !/^steam_api64\.dll$/i.test(ModuleInfo.name)) return;
    SteamObserved = true;
    send({event:'steam_api_module_loaded'});
    for (const Name of ['SteamAPI_RestartAppIfNecessary', 'SteamAPI_Init']) {
      const Address = ModuleInfo.findExportByName(Name);
      if (!Address) continue;
      Hooks.push(Interceptor.attach(Address, {onLeave(Result) {
        send({event:Name==='SteamAPI_RestartAppIfNecessary'
          ? ((Result.toUInt32()&255)!==0?'steam_restart_requested':'steam_restart_not_required')
          : ((Result.toUInt32()&255)!==0?'steam_initialization_succeeded':'steam_initialization_failed')});
      }}));
    }
  }});
  const Shell = Module.load('shell32.dll'), Ole = Module.load('ole32.dll'), Winsock = Module.load('ws2_32.dll');
  const Allocate = new NativeFunction(Ole.getExportByName('CoTaskMemAlloc'),'pointer',['uint64']);
  const Free = new NativeFunction(Ole.getExportByName('CoTaskMemFree'),'void',['pointer']);
  const DocumentsId = 'd09ad3fd8f23af46adb46c85480369c7';
  function Hex(P) { return Array.from(new Uint8Array(P.readByteArray(16)), B=>B.toString(16).padStart(2,'0')).join(''); }
  Hooks.push(Interceptor.attach(Shell.getExportByName('SHGetKnownFolderPath'), {
    onEnter(Args) { this.Match=Hex(Args[0])===DocumentsId; this.Out=Args[3]; },
    onLeave(Result) {
      if (!this.Match || this.Out.isNull()) return;
      const Replacement=Allocate((Config.documents.length+1)*2);
      if (Replacement.isNull()) throw new Error('Private Documents allocation failed');
      Replacement.writeUtf16String(Config.documents);
      if (Result.toInt32()>=0 && !this.Out.readPointer().isNull()) Free(this.Out.readPointer());
      this.Out.writePointer(Replacement); Result.replace(0);
    }
  }));
  // Older clients can use the CSIDL wrapper instead of known-folder IDs.
  Hooks.push(Interceptor.attach(Shell.getExportByName('SHGetFolderPathW'), {
    onEnter(Args) { this.Match=(Args[1].toUInt32()&0xff)===5; this.Out=Args[4]; },
    onLeave(Result) { if(this.Match && !this.Out.isNull()) { this.Out.writeUtf16String(Config.documents);Result.replace(0); } }
  }));
  const Ports=new Set([25100,25500,25501,25502]);
  let NetworkReports=0;
  function Destination(Address,Length) {
    if(Address.isNull() || Length<2) return {family:0,port:0,loopback:false};
    const Family=Address.readU16();
    return {family:Family,
      port:Length>=4 && (Family===2 || Family===23)?(Address.add(2).readU8()<<8)|Address.add(3).readU8():0,
      loopback:Family===2 && Length>=8 && Address.add(4).readU8()===127};
  }
  function Allowed(Address,Length) {
    if(Address.isNull() || Length<16 || Address.readU16()!==2) return false;
    const Port=(Address.add(2).readU8()<<8)|Address.add(3).readU8();
    return Ports.has(Port) && Address.add(4).readU8()===127 && Address.add(5).readU8()===0 &&
      Address.add(6).readU8()===0 && Address.add(7).readU8()===1;
  }
  function Guard(Address,ReturnType,Types,AddressIndex,LengthIndex,Failure,ConnectedAllowed=false,Operation='send') {
    const Original=new SystemFunction(Address,ReturnType,Types);
    const Callback=new NativeCallback(function(...Args) {
      if(!(ConnectedAllowed && Args[AddressIndex].isNull()) && !Allowed(Args[AddressIndex],Args[LengthIndex])) {
        if(NetworkReports++<16) send({event:'private_network_result',operation:Operation,error:10013,result:Failure,blocked:true,
          ...Destination(Args[AddressIndex],Args[LengthIndex])});
        this.lastError=10013; return Failure;
      }
      // NativeFunction discards the captured OS error. Nonblocking connect
      // returns SOCKET_ERROR with WSAEWOULDBLOCK, and ConnectEx uses IO_PENDING;
      // the caller needs BOTH the return value and the preserved error code.
      const Result=Original(...Args);
      if(Operation!=='send' && NetworkReports++<16)
        send({event:'private_network_result',operation:Operation,error:Result.lastError,result:Result.value,blocked:false,
          ...Destination(Args[AddressIndex],Args[LengthIndex])});
      this.lastError=Result.lastError;
      return Result.value;
    },ReturnType,Types);
    Callbacks.push(Callback); Interceptor.replace(Address,Callback);
  }
  Guard(Winsock.getExportByName('connect'),'int',['uint64','pointer','int'],1,2,-1,false,'connect');
  Guard(Winsock.getExportByName('WSAConnect'),'int',['uint64','pointer','int','pointer','pointer','pointer','pointer'],1,2,-1,false,'wsa_connect');
  Guard(Winsock.getExportByName('sendto'),'int',['uint64','pointer','int','int','pointer','int'],4,5,-1,true);
  Guard(Winsock.getExportByName('WSASendTo'),'int',['uint64','pointer','uint','pointer','uint','pointer','int','pointer','pointer'],5,6,-1,true);
  const ConnectId='b907a225f3dd60468ee976e58c74063e', Seen=new Set();
  Hooks.push(Interceptor.attach(Winsock.getExportByName('WSAIoctl'),{
    onEnter(Args) {
      this.Match=Args[1].toUInt32()===0xc8000006 && Args[3].toUInt32()===16 && Hex(Args[2])===ConnectId;
      this.Out=Args[4]; this.Size=Args[5].toUInt32();
    },
    onLeave(Result) {
      if(!this.Match || Result.toInt32()!==0 || this.Size<8 || this.Out.isNull()) return;
      const Address=this.Out.readPointer(), Key=Address.toString();
      if(Address.isNull() || Seen.has(Key)) return;
      if(Seen.size>=8) throw new Error('Private ConnectEx limit');
      Seen.add(Key);
      Guard(Address,'int',['uint64','pointer','int','pointer','uint','pointer','pointer'],1,2,0,false,'connect_ex');
    }
  }));
  // Keep references for the process lifetime.
  globalThis.PrivateWindowsEnvironment={Hooks,Callbacks,PlatformObserver,ready:true};
  send({event:'windows_private_environment_armed'});
})();
