/* Private Friends manager adapter. See Research/PartyServices/native-friends-menu-nov9.txt.
 * The unsecure private login does not tick the retail RequestFriendsList service.
 * Reconcile through the game's own friend/presence callbacks on a native UI call.
 * No executable patches, SDK calls, captured identities, or RPC-thread engine calls.
 */
'use strict';
function createPrivateFriendsBridge(game, log, threads) {
  const keep = [], maximum = 256;
  let entries = [], generation = 0, appliedGeneration = -1, owner = ptr(0);
  let appliedRevision = -1, applied = new Map(), busy = false, received = false, reported = false, verified = false;
  let retryAt = 0, stage = 'context';
  function bytes(rva,n) { return Array.from(new Uint8Array(game.base.add(rva).readVolatile(n)), x=>x.toString(16).padStart(2,'0')).join(''); }
  const signatures = [
    [0x13257b0,'488b05713336034885c07408'], // current game context
    [0x175e260,'48895c2410574883ec60ff81'], // friend changed; owns creation/revision
    [0x18dc1a0,'48895c2418574883ec60488b'], // presence changed
    [0x185ae70,'405356574883ec20488bf948'], // friend removed; owns destruction
    [0x1855ae0,'40534883ec4048896c245848'], // native lookup (create=false below)
    [0xdfbc0,'48895c240848897424105748'], // row -> platform ID
    [0x19ba650,'40564883ec20'],            // FriendsOnline UI evaluator
    [0x1689780,'4883ec48488b41288b501c0f'], // selected profile input
    [0x17c3720,'40534883ec20488bcae85260'], // ToggleMute
    [0x17a28e0,'41574883ec304c8b7a204d85'], // IsMuted
    [0x1bb9640,'4881c150020000e9c4794bfe'], // native voice mute query
    [0x1bb9c00,'48895c240848896c24104889']  // native voice mute setter
  ];
  const slot=game.base.add(0x32e5338), handler=game.base.add(0x19ba650);
  if(signatures.some(([rva,prefix])=>bytes(rva,prefix.length/2)!==prefix) ||
     !slot.readPointer().equals(handler) ||
     !game.base.add(0x3217a88).readPointer().equals(game.base.add(0x17c3720)) ||
     !game.base.add(0x32170c8).readPointer().equals(game.base.add(0x17a28e0)))
    throw new Error('Private friend manager build mismatch');
  const native=(rva,result,args)=>new NativeFunction(game.base.add(rva),result,args,{scheduling:'exclusive'});
  const current=native(0x13257b0,'pointer',[]), upsert=native(0x175e260,'void',['pointer','pointer']);
  const presence=native(0x18dc1a0,'void',['pointer','pointer']), remove=native(0x185ae70,'void',['pointer','pointer']);
  const platformId=native(0xdfbc0,'pointer',['pointer','pointer']);
  const lookup=native(0x1855ae0,'pointer',['pointer','pointer','uint8','pointer']);
  const original=native(0x19ba650,'void',['pointer','pointer']);
  const selected=native(0x1689780,'pointer',['pointer']);
  const muted=native(0x1bb9640,'uint8',['pointer','pointer']);
  const setMuted=native(0x1bb9c00,'void',['pointer','pointer','uint8']);
  // Private mode may not construct a voice manager at startup. Retain the
  // user's session preference and apply it when the real manager is available.
  // Never claim that this supplies voice transport or mute the entire game.
  const mutePreferences=new Map(), muteId=Memory.alloc(0x40);keep.push(muteId);
  let muteRefreshAt=0;
  function privateKey(profile) {
    if(profile.isNull())return null;
    const identity=profile.add(0x18);
    if(identity.readU8()!==2 || identity.add(1).readU8()!==36)return null;
    const key=identity.add(2).readUtf8String(36).toLowerCase();
    return entries.some(entry=>entry.accountId===key)?key:null;
  }
  function muteIdentity(key) {muteId.writeU8(2);muteId.add(1).writeU8(36);muteId.add(2).writeUtf8String(key);return muteId;}
  function applyMutePreferences(context) {
    if(Date.now()<muteRefreshAt)return;
    muteRefreshAt=Date.now()+1000;
    const voice=context.add(0x130).readPointer();if(voice.isNull())return;
    for(const [key,value] of mutePreferences) {
      const identity=muteIdentity(key);
      if(!!muted(voice,identity)!==value)setMuted(voice,identity,value?1:0);
    }
  }
  function wrap(table,rva,invoke) {
    const target=game.base.add(table+0x70), expected=game.base.add(rva);
    if(!target.readPointer().equals(expected))throw new Error('Private social action build mismatch');
    const call=native(rva,'void',['pointer','pointer']);
    const callback=new NativeCallback((self,context)=>invoke(call,self,context),'void',['pointer','pointer']);
    const range=Process.findRangeByAddress(target);
    if(!range||!Memory.protect(target,8,'rw-'))throw new Error('Private social UI protection');
    keep.push(callback);
    try{target.writePointer(callback);}finally{Memory.protect(target,8,range.protection);}
  }
  wrap(0x3217a18,0x17c3720,(original,self,context)=>{
    let key=null;
    try {
      const input=selected(context), handle=input.readPointer();
      if(!handle.isNull())key=privateKey(handle.add(0x50).readPointer());
      if(key) {
        const root=current(), voice=root.isNull()?ptr(0):root.add(0x130).readPointer();
        const previous=mutePreferences.has(key)?mutePreferences.get(key):
          !voice.isNull()&&!!muted(voice,muteIdentity(key));
        const value=!previous;mutePreferences.set(key,value);
        if(!voice.isNull())setMuted(voice,muteIdentity(key),value?1:0);
        log(value?'private_friend_muted':'private_friend_unmuted');return;
      }
    } catch (_) {log('private_friend_mute_error');}
    original(self,context);
  });
  wrap(0x3217058,0x17a28e0,(original,self,context)=>{
    original(self,context);
    try {
      const input=context.add(0x28).readPointer(), output=context.add(0x20).readPointer();
      if(input.isNull()||output.isNull())return;
      const inputFlags=input.add(0x1c).readU32(), outputFlags=output.add(0x1c).readU32();
      const count=inputFlags&0xffff;if(count>maximum||count!==(outputFlags&0xffff))return;
      const source=inputFlags&0x10000000?input.readPointer():input;
      const destination=outputFlags&0x10000000?output.readPointer():output;
      for(let i=0;i<count;i++) {
        const handle=source.add(i*8).readPointer();if(handle.isNull())continue;
        const key=privateKey(handle.add(0x50).readPointer());
        if(key&&mutePreferences.has(key))destination.add(i).writeU8(mutePreferences.get(key)?1:0);
      }
    } catch (_) {log('private_friend_mute_display_error');}
  });
  // Scratch inputs are copied synchronously by native callbacks. Each fixed
  // string has separate 65-byte storage so a 64-byte name retains its terminator.
  const row=Memory.alloc(0x110), status=Memory.alloc(0x110), id=Memory.alloc(0x40);
  keep.push(row,status,id);
  for(const buffer of [row,status])for(const offset of [0,0x58,0xb0]) {
    const string=buffer.add(offset), storage=Memory.alloc(65);keep.push(storage);
    string.writePointer(game.base.add(0x2910bb8));string.add(0x48).writePointer(storage);
    string.add(0x50).writeU32(65);storage.writeUtf8String('');
  }
  function string(buffer,offset,value){buffer.add(offset+0x48).readPointer().writeUtf8String(value);}
  function prepare(entry) {
    string(row,0,'');string(row,0x58,entry.accountId);string(row,0xb0,entry.name);
    // 1 is an accepted, unblocked friend. 2 is excluded by FriendsOnline and
    // causes native friend removal. It is NOT the online flag used by the SDK bridge.
    row.add(0x108).writeU32(1);row.add(0x10c).writeU8(0);row.add(0x10d).writeU8(1);
    string(status,0,'');string(status,0x58,'');string(status,0xb0,entry.accountId);
    // Native presence: 0 offline, 1 in game, 2 online outside game (6AFF9/6B005).
    status.add(0x108).writeU32(entry.online?1:0);
  }
  function refresh() {
    if(busy || Date.now()<retryAt)return;
    busy=true;
    try {
      stage='context';
      const context=current();
      if(context.isNull())return;
      applyMutePreferences(context);
      const manager=context.add(0x1a8).readPointer();
      const shared=context.add(0x28).readPointer();
      // Constructor also requires these live context services and the manager's
      // identity registry. Wait for ordinary game initialization to finish.
      if(manager.isNull() || shared.isNull() || shared.add(0x340).readPointer().isNull() ||
         context.add(0x110).readPointer().isNull() ||
         manager.add(0x238).readPointer().isNull() || manager.add(0x260).readPointer().isNull())return;
      if(!owner.equals(manager)){owner=manager;applied=new Map();appliedGeneration=-1;appliedRevision=-1;}
      const revision=manager.add(0x3f8).readU32();
      if(appliedGeneration===generation && appliedRevision===revision)return;
      const desired=new Map(entries.map(entry=>[entry.accountId,entry]));
      let removedCount=0;
      for(const [key,entry] of applied)if(!desired.has(key)) {
        stage='remove';
        prepare(entry);platformId(row,id);remove(manager,id);applied.delete(key);removedCount++;
      }
      for(const entry of entries) {
        stage='prepare';prepare(entry);
        stage='upsert';upsert(manager,row);
        stage='presence';presence(manager,status);
        stage='identity';
        platformId(row,id);
        stage='lookup';
        const friend=lookup(manager,id,0,ptr(0));
        stage='retained';
        if(friend.isNull() || friend.add(0x120).readU32()!==1 ||
           friend.add(0x230).readU32()!==(entry.online?1:0))
          throw new Error('Native private friend not retained');
        applied.set(entry.accountId,entry);
      }
      appliedGeneration=generation;appliedRevision=manager.add(0x3f8).readU32();
      if(!reported || entries.length) {
        reported=true;log('private_friends_manager_reconciled');
      }
      if(entries.length && !verified){verified=true;log('private_friends_manager_entries_present');}
      if(removedCount)log('private_friends_manager_entries_removed');
    } catch (_) { retryAt=Date.now()+5000;log('private_friends_manager_error_'+stage); }
    finally {busy=false;}
  }
  const callback=new NativeCallback((self,context)=>{
    refresh();original(self,context);
  },'void',['pointer','pointer']);keep.push(callback);
  const range=Process.findRangeByAddress(slot);
  if(!range || !Memory.protect(slot,8,'rw-'))throw new Error('Private friend UI slot protection');
  try{slot.writePointer(callback);}finally{Memory.protect(slot,8,range.protection);}
  function update(value) {
    if(!Array.isArray(value)||value.length>maximum)throw new Error('Invalid private friend roster');
    const seen=new Set();
    const next=value.map(row=>{
      const key=typeof row?.accountId==='string'?row.accountId.toLowerCase():'';
      if(!(/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(key))||
         key==='00000000-0000-0000-0000-000000000000'||seen.has(key)||
         typeof row.name!=='string'||!(/^[\x20-\x7e]{1,64}$/).test(row.name)||typeof row.online!=='boolean')
        throw new Error('Invalid private friend entry');
      seen.add(key);return {accountId:key,name:row.name,online:row.online};
    });
    if(JSON.stringify(entries)!==JSON.stringify(next)){entries=next;generation++;}
    if(entries.length&&!received){received=true;log('private_friends_roster_received');}
  }
  log('private_friends_provider_installed');
  // Keep the startup adapter interface; this path consumes no debug registers.
  return {update,refresh,added(){},removed(){},exception(){return false;},keep};
}
