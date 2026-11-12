/* Build-specific independent client startup. Derived from the checked prototype
 * startup_options_agent.js. Changes the defaults-parser argument in memory,
 * then supplies the private gamertag/readiness and zero private achievement
 * score to the account UI nodes. Never edits
 * the executable on disk, manufactures a platform ticket, or redirects live traffic.
 * This adapter is used only in the launcher's isolated private-server process.
 */
'use strict';
const cfg = globalThis.PRIVATE_STARTUP_CONFIG;
const game = Process.enumerateModules().find(m => /^thedivision\.exe$/i.test(m.name));
if (!game || Process.arch !== 'x64' || !cfg.defaults.includes('-proxylist 127.0.0.1 '))
  throw new Error('Unsupported private client startup');
const entry = game.base.add(0x8591), replacement = Memory.allocUtf8String(cfg.defaults);
const threads = new Map(), callbacks = [], strings = [];
let done = false, failed = false, widgets = false;
let privateFriends = null, pendingFriends = [];
function hex(p,n) { return Array.from(new Uint8Array(p.readVolatile(n)),b=>b.toString(16).padStart(2,'0')).join(''); }
function event(name) { send({event:name}); } // No ticket, password, command line or account PII in diagnostics.
const aimDiagnostic = cfg.aimActor ? createAimDiagnostic(game, cfg.aimActor, send) : null;
function installWidgets() {
  if (!cfg.displayName || /[\u0000-\u001f\u007f]/.test(cfg.displayName)) throw new Error('Invalid label');
  const label = Memory.allocUtf8String(cfg.displayName); strings.push(label);
  const assign = new NativeFunction(game.base.add(0x116830),'pointer',['pointer','pointer'],{scheduling:'exclusive'});
  const specs = [
    {table:0x32eecb8,fn:0x19bba40,prefix:'4055488d6c24a9',kind:'name'},
    {table:0x32ef678,fn:0x19bc670,prefix:'48895c240857',kind:'ready'},
    // RClient_FrontendNodeGetCachedAchievementScore, not the wallet getter.
    // Original19bb4a0 reads frontend account+468 and writes an int32 output.
    // Private achievements are not implemented: expose zero, never retail cache.
    {table:0x32f3ab8,fn:0x19bb4a0,prefix:'48895c2408574883ec20',kind:'score'}
  ];
  for (const s of specs)
    if (!game.base.add(s.table+0x70).readPointer().equals(game.base.add(s.fn)) || hex(game.base.add(s.fn),s.prefix.length/2)!==s.prefix)
      throw new Error('Account UI build mismatch');
  for (const s of specs) {
    const original = new NativeFunction(game.base.add(s.fn),'void',['pointer','pointer'],{scheduling:'exclusive'});
    const callback = new NativeCallback((self,context)=>{
      try {
        if (privateFriends) privateFriends.refresh();
        original(self,context);
        const output=context.add(0x20).readPointer();
        if (!output.isNull() && (output.add(0x1c).readU32()&0x1000ffff)===1) {
          if (s.kind==='name') assign(output,label);
          else if (s.kind==='score') output.writeS32(0);
          else output.writeU8(1);
        }
      } catch (_) { event('account_widget_error'); }
    },'void',['pointer','pointer']);
    callbacks.push(callback);
    const slot=game.base.add(s.table+0x70), range=Process.findRangeByAddress(slot);
    if (!range || !Memory.protect(slot,8,'rw-')) throw new Error('Account UI protection');
    try { slot.writePointer(callback); } finally { Memory.protect(slot,8,range.protection); }
  }
  if (cfg.privateFriends && !aimDiagnostic) {
    // Install native interceptors outside the startup exception-handler stack.
    setImmediate(() => {
      try { privateFriends=createPrivateFriendsBridge(game,event,threads);privateFriends.update(pendingFriends); }
      catch (_) { event('private_friends_provider_unavailable'); }
    });
  }
  widgets=true; event('private_account_widgets_installed');
}
let faultCount = 0;
Process.setExceptionHandler(details=>{
  if (privateFriends && privateFriends.exception(details)) return true;
  if (aimDiagnostic && aimDiagnostic.exception(details)) return true;
  if (!details.context.pc.equals(entry)) {
    if (cfg.diagnostics && faultCount < 4 && details.type !== 'single-step' &&
        details.context.pc.compare(game.base) >= 0 && details.context.pc.compare(game.base.add(game.size)) < 0) {
      faultCount++;
      const rva = details.context.pc.sub(game.base).toUInt32();
      const fault = {event:'client_fault', type:details.type, rva:rva};
      // This checked game's fatal HRESULT helper still carries its HRESULT in
      // ECX. Other fault sites must not dump arbitrary register contents.
      if (rva === 0x36c490) fault.hresult = details.context.rcx.toUInt32();
      // Shared graphics HRESULT abort: cmp eax, DEVICE_REMOVED; jne abort;
      // load device; call GetDeviceRemovedReason; deliberate null write.
      // Only the RIP-relative device address varies. EAX retains the failed
      // HRESULT or removal reason at this exact instruction sequence. Covers
      // CreateShaderResourceView (356bae), Map (371a93), and identical branches
      // without logging arbitrary registers from unrelated access violations.
      if (rva >= 23 && rva <= game.size-11 &&
          /^3d05007a887510488b0d[0-9a-f]{8}488b01ff9098010000c704250000000000000000$/.test(
            hex(game.base.add(rva-23),34)))
        fault.hresult = details.context.rax.toUInt32();
      send(fault);
    }
    return false;
  }
  const thread=threads.get(Process.getCurrentThreadId());
  if (!thread) return false;
  thread.unsetHardwareBreakpoint(0);
  if (done || failed) return true;
  try {
    if(hex(game.base.add(0x858a),12)!=='488d0def7a9002e8ca482500' ||
        !details.context.rcx.equals(game.base.add(0x2910080)) ||
        !game.base.add(0x42b93b8).readPointer().isNull() || details.context.rcx.readUtf8String()!==cfg.expectedDefaults)
      throw new Error('Client startup build mismatch');
    details.context.rcx=replacement; done=true; event('startup_defaults_replaced');
    installWidgets();
  } catch (_) { failed=true; event('startup_override_rejected'); }
  return true;
});
const observer=Process.attachThreadObserver({
  onAdded(thread) {
    threads.set(thread.id,thread);
    if (privateFriends) {
      try { privateFriends.added(thread); }
      catch (_) { event('private_friends_thread_unavailable'); }
    }
    if (aimDiagnostic) aimDiagnostic.added(thread);
    try {
      if(!done&&!failed)thread.setHardwareBreakpoint(0,entry);
    } catch (_) {failed=true;}
  },
  onRemoved(thread) {threads.delete(thread.id); if(privateFriends)privateFriends.removed(thread); if (aimDiagnostic) aimDiagnostic.removed(thread);}
});
// Opt-in bounded metadata inspection. Reads event type/codec addresses only;
// never executes a game codec or reads account/session/packet contents.
function packetMetadata() {
  if (!done || failed) throw new Error('Startup incomplete');
  const rows=[];
  for(let rva=0x3190000;rva<0x31b0000;rva+=8) {
    try {
      const vt=game.base.add(rva), getter=vt.add(0x18).readPointer();
      if(getter.compare(game.base)<0 || getter.compare(game.base.add(0x2900000))>=0)continue;
      if(hex(getter,3)!=='0fb705'||getter.add(7).readU8()!==0xc3)continue;
      const id=getter.add(7).add(getter.add(3).readS32()).readU16();
      if(id>4096)continue;
      const col=vt.sub(8).readPointer(), name=game.base.add(col.add(12).readU32()+16);
      let type='';
      for(let n=0;n<240;n++) {const c=name.add(n).readU8();if(c===0)break;if(c<32||c>126)throw new Error('Invalid type');type+=String.fromCharCode(c);}
      rows.push({message_id:id,type_name:type,vtable_rva:rva,
        reader_rva:vt.add(0x38).readPointer().sub(game.base).toUInt32(),
        writer_rva:vt.add(0x30).readPointer().sub(game.base).toUInt32(),
        validator_rva:vt.add(8).readPointer().sub(game.base).toUInt32()});
    } catch (_) {}
  }
  return rows;
}
rpc.exports={privatefriends:entries=>{
  if (!Array.isArray(entries)||entries.length>256) throw new Error('Invalid private friend roster');
  if(privateFriends)privateFriends.update(entries);
  pendingFriends=entries;
},packetmetadata:packetMetadata,
  aimtrace() { if (!done || failed || !aimDiagnostic) throw new Error('Aim trace unavailable'); aimDiagnostic.start(); },
  status() {return {armed:threads.size,done,failed,widgets};}};
