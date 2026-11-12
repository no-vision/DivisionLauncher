'use strict';
// Loaded only by the isolated private adapter, before the client is resumed.
// Creation calls use JS; the per-frame callbacks are native CModule functions.
globalThis.PrivateOverlay = (() => {
  const Hooks = [], Seen = new Set(), Modules = [], Storage = Memory.alloc(16 * 1024 * 1024);
  const Lock = Memory.alloc(40), Counters = Memory.alloc(32);
  let ModuleObserver = null, Native = null, Publish = null, ReadCounters = null, ReadInput = null, ReadInputStatus = null, Heartbeat = null, Failure = null;
  const InputCounters = Memory.alloc(24);
  const Input = Memory.alloc((5 + 64 * 5) * 4);
  const Gui = Process.findModuleByName('thedivision.exe');
  function Emit(Name) { send({event: Name}); }
  function Method(Object, Slot) { return Object.readPointer().add(Slot * 8).readPointer(); }
  function Attach(Address, Kind, Callbacks) {
    const Key = Address.toString() + ':' + Kind;
    if (Seen.has(Key)) return;
    if (Seen.size >= 64) throw new Error('overlay_hook_limit');
    // A single implementation cannot be attached with two incompatible signatures.
    for (const Entry of Seen) if (Entry.startsWith(Address.toString() + ':')) return;
    const Range = Process.findRangeByAddress(Address);
    if (!Range || !Range.protection.includes('x')) throw new Error('overlay_nonexecutable_method');
    Hooks.push(Interceptor.attach(Address, Callbacks)); Seen.add(Key);
  }
  function Query(Object, Id, Consume) {
    const Out = Memory.alloc(8), Guid = Memory.alloc(16);
    Guid.writeByteArray(Id);
    const Result = new NativeFunction(Method(Object,0),'int',['pointer','pointer','pointer'])(Object,Guid,Out);
    if (Result < 0 || Out.readPointer().isNull()) return;
    const Interface = Out.readPointer();
    try { Consume(Interface); }
    finally { new NativeFunction(Method(Interface,2),'uint',['pointer'])(Interface); }
  }
  function Chain(Object) {
    if (Object.isNull()) return;
    Attach(Method(Object,8),'present',{onEnter:Native.OnPresent});
    // IDXGISwapChain1; a failed QI is expected on older chains.
    Query(Object,[0xf7,0x45,0x0a,0x79,0x42,0x0d,0x76,0x48,0x98,0x3a,0x0a,0x55,0xcf,0xe6,0xf4,0xaa],
      Interface => Attach(Method(Interface,22),'present1',{onEnter:Native.OnPresent1}));
    // DXGI_SWAP_CHAIN_DESC: Mode28 + Sample8 + Usage4 + Count4 + alignment4;
    // OutputWindow is at48 on x64, NOT the BufferCount field at40.
    const Desc = Memory.alloc(72);
    if (new NativeFunction(Method(Object,12),'int',['pointer','pointer'])(Object,Desc) >= 0)
      Emit(new NativeFunction(Native.InstallWindow,'int',['pointer'])(Desc.add(48).readPointer()) ?
        'overlay_input_window_installed' : 'overlay_input_window_failed');
    Emit('overlay_swapchain_observed');
  }
  function Creation(Address, OutputIndex, Kind, Consume) {
    Attach(Address,Kind,{
      onEnter(Args) { this.Output = Args[OutputIndex]; },
      onLeave(Result) {
        if (Result.toInt32() < 0 || this.Output.isNull()) return;
        try { Consume(this.Output.readPointer()); }
        catch (_) { Emit('overlay_creation_probe_failed'); }
      }
    });
  }
  function Factory(Object) {
    if (Object.isNull()) return;
    Creation(Method(Object,10),3,'create_chain',Chain);
    // IDXGIFactory2: CreateSwapChainForHwnd. No guessed vtable extension.
    Query(Object,[0x1c,0x3a,0xc8,0x50,0x72,0xe0,0x48,0x4c,0x87,0xb0,0x36,0x30,0xfa,0x36,0xa6,0xd0],
      Interface => Creation(Method(Interface,15),6,'create_chain_hwnd',Chain));
  }
  function InputFactory(Object) {
    if (Object.isNull()) return;
    Attach(Method(Object,3),'input_create_device',{
      onEnter(Args) {
        this.Output = Args[2];
        const Bytes = Array.from(new Uint8Array(Args[1].readByteArray(16)));
        const Tail = [0x2b,0x1d,0x6f,0xa0,0xd5,0xcf,0x11,0xbf,0xc7,0x44,0x45,0x53,0x54,0,0];
        this.Kind = Bytes.slice(1).every((Byte,Index)=>Byte===Tail[Index]) ?
          (Bytes[0]===0x60 ? 1 : Bytes[0]===0x61 ? 2 : 0) : 0;
      },
      onLeave(Result) {
        if (Result.toInt32()<0 || !this.Kind || this.Output.isNull()) return;
        try {
          const Device=this.Output.readPointer();
          if (Device.isNull() || !new NativeFunction(Native.RegisterInput,'int',['pointer','uint'])(Device,this.Kind)) return;
          Attach(Method(Device,9),'input_state',{onEnter:Native.OnInputStateEnter,onLeave:Native.OnInputStateLeave});
          Attach(Method(Device,10),'input_data',{onEnter:Native.OnInputDataEnter,onLeave:Native.OnInputDataLeave});
          Attach(Method(Device,11),'input_format',{onEnter:Native.OnInputFormatEnter,onLeave:Native.OnInputFormatLeave});
          Emit(this.Kind===1?'overlay_direct_mouse_observed':'overlay_direct_keyboard_observed');
        } catch (_) { Emit('overlay_input_probe_failed'); }
      }
    });
  }
  function Observe(Module) {
    const Name = Module.name.toLowerCase();
    if (Name !== 'dxgi.dll' && Name !== 'd3d11.dll' && Name !== 'dinput8.dll') return;
    Modules.push(Name);
    try {
      if (Name === 'dinput8.dll') {
        const Address = Module.findExportByName('DirectInput8Create');
        if (Address) Creation(Address,3,'create_input',InputFactory);
      } else if (Name === 'd3d11.dll') {
        const Address = Module.findExportByName('D3D11CreateDeviceAndSwapChain');
        if (Address) Creation(Address,8,'create_device_chain',Chain);
      } else {
        for (const Name of ['CreateDXGIFactory','CreateDXGIFactory1','CreateDXGIFactory2']) {
          const Address = Module.findExportByName(Name);
          if (Address) Creation(Address,1,'create_factory',Factory);
        }
      }
      Emit('overlay_render_module_observed');
    } catch (_) { Emit('overlay_module_probe_failed'); }
  }
  try {
    if (Process.arch !== 'x64' || Process.platform !== 'windows' || !Gui ||
        !globalThis.PRIVATE_STARTUP_CONFIG?.defaults.includes('-proxylist 127.0.0.1 '))
      throw new Error('overlay_private_process_required');
    const Kernel = Process.getModuleByName('kernel32.dll');
    const User = Process.getModuleByName('user32.dll');
    new NativeFunction(Kernel.getExportByName('InitializeCriticalSection'),'void',['pointer'])(Lock);
    Native = new CModule(globalThis.PRIVATE_OVERLAY_SOURCE, {
      State: Storage, RenderLock: Lock,
      TryLock: Kernel.getExportByName('TryEnterCriticalSection'),
      Unlock: Kernel.getExportByName('LeaveCriticalSection'),
      ClockMilliseconds: Kernel.getExportByName('GetTickCount64'),
      KeyState: User.getExportByName('GetAsyncKeyState'), ForegroundWindow: User.getExportByName('GetForegroundWindow'),
      SetWindowProcedure: User.getExportByName('SetWindowLongPtrW'), CallWindowProcedure: User.getExportByName('CallWindowProcW'),
      DefaultWindowProcedure: User.getExportByName('DefWindowProcW'), ReadRawInput: User.getExportByName('GetRawInputData'),
      CursorPosition: User.getExportByName('GetCursorPos'), ClientPosition: User.getExportByName('ScreenToClient'),
      ClientBounds: User.getExportByName('GetClientRect'), CursorInformation: User.getExportByName('GetCursorInfo')
    });
    if (new NativeFunction(Native.StateSize,'uint',[])() > 16 * 1024 * 1024) throw new Error('overlay_state_size');
    Publish = new NativeFunction(Native.Publish,'int',['uint','uint','uint','uint','uint','pointer','uint']);
    ReadCounters = new NativeFunction(Native.Snapshot,'int',['pointer']);
    ReadInput = new NativeFunction(Native.InputSnapshot,'int',['pointer']);
    ReadInputStatus = new NativeFunction(Native.InputStatus,'int',['pointer']);
    Heartbeat = new NativeFunction(Native.Heartbeat,'void',[]);
    // Compile once, outside Present. No source includes, filesystem or arbitrary
    // user shaders; this authored triangle samples only our RGBA panel texture.
    const Compiler = Module.load('d3dcompiler_47.dll');
    const Compile = new NativeFunction(Compiler.getExportByName('D3DCompile'),'int',
      ['pointer','uint64','pointer','pointer','pointer','pointer','pointer','uint','uint','pointer','pointer']);
    const ShaderSource = 'struct V { float4 p:SV_Position; float2 uv:TEXCOORD0; }; ' +
      'V VS(uint id:SV_VertexID) { V o; o.uv=float2((id<<1)&2,id&2); o.p=float4(o.uv*float2(2,-2)+float2(-1,1),0,1); return o; } ' +
      'Texture2D Panel:register(t0); float4 PS(V i):SV_Target { uint w,h; Panel.GetDimensions(w,h); return Panel.Load(int3(int2(i.uv*float2(w,h)),0)); }';
    const Source = Memory.allocUtf8String(ShaderSource), Blobs = [];
    try {
      for (const [Entry, Target] of [['VS','vs_4_0'],['PS','ps_4_0']]) {
        const Code = Memory.alloc(8), Errors = Memory.alloc(8);
        const Result = Compile(Source,ShaderSource.length,ptr(0),ptr(0),ptr(0),Memory.allocUtf8String(Entry),Memory.allocUtf8String(Target),0,0,Code,Errors);
        if (!Errors.readPointer().isNull()) new NativeFunction(Method(Errors.readPointer(),2),'uint',['pointer'])(Errors.readPointer());
        if (!Code.readPointer().isNull()) Blobs.push(Code.readPointer());
        if (Result < 0 || Code.readPointer().isNull()) throw new Error('overlay_shader_compile');
      }
      const Data = Blobs.map(Blob => [new NativeFunction(Method(Blob,3),'pointer',['pointer'])(Blob),
        Number(new NativeFunction(Method(Blob,4),'uint64',['pointer'])(Blob))]);
      if (!new NativeFunction(Native.Shaders,'int',['pointer','uint','pointer','uint'])(Data[0][0],Data[0][1],Data[1][0],Data[1][1]))
        throw new Error('overlay_shader_size');
    } finally { for (const Blob of Blobs) new NativeFunction(Method(Blob,2),'uint',['pointer'])(Blob); }
    ModuleObserver = Process.attachModuleObserver({onAdded:Observe});
    const Frame = Memory.alloc(1200 * 900 * 4);
    function Receive() {
      recv('overlay-frame', (Message, Data) => {
        const {index,x,y,width,height} = Message;
        if ([index,x,y,width,height].every(Number.isInteger) && index >= 0 && index < 3 && x >= 0 && y >= 0 &&
            width >= 0 && height >= 0 && width <= 1200 && height <= 900 && (Data?.byteLength || 0) === width*height*4) {
          if (Data?.byteLength) Frame.writeByteArray(Data);
          Publish(index,x,y,width,height,Frame,Data?.byteLength || 0);
        }
        Receive();
      });
    }
    Receive(); Emit('overlay_bridge_armed');
  } catch (_) {
    Failure = 'overlay_initialization_failed';
    for (const Hook of Hooks) Hook.detach();
    ModuleObserver?.detach(); Emit(Failure);
  }
  return {
    input() {
      if (!ReadInput) return {events:[]};
      Heartbeat();
      const Count = ReadInput(Input);
      if (Count < 0) return {events:[]};
      const Get = Index => Input.add(Index*4).readS32();
      return {screen:[Get(0),Get(1)],edit:!!(Get(2)&1),softwareCursor:!!(Get(2)&2),mouse:[Get(3),Get(4)],
        events:Array.from({length:Count},(_,Row)=>Array.from({length:5},(_,Column)=>Get(5+Row*5+Column)))};
    },
    status() {
      const Values = ReadCounters && ReadCounters(Counters) ?
        Array.from({length:8},(_,Index)=>Counters.add(Index*4).readU32()) : null;
      const InputValues = ReadInputStatus && ReadInputStatus(InputCounters) ?
        Array.from({length:6},(_,Index)=>InputCounters.add(Index*4).readU32()) : null;
      return {failure:Failure,modules:Modules.slice(0,8),hooks:Seen.size,counters:Values,
        input:InputValues && {window:!!InputValues[0],mouse:!!InputValues[1],keyboard:!!InputValues[2],edit:!!InputValues[3],devices:InputValues[4],formats:InputValues[5]}};
    }
  };
})();
