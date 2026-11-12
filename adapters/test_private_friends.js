// Contract fixture, not proof of native menu rendering. Byte guards are checked
// against the pinned native image separately in the research evidence.
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const memory=Buffer.alloc(0x5000000),callbacks=new Map(),calls=[],friends=new Map();
let next=0x4400000,live=false,manager=0x4100000,now=0;
class Pointer {
 constructor(value){this.value=value;}
 add(n){return new Pointer(this.value+n);}
 equals(p){return this.value===p.value;}
 isNull(){return this.value===0;}
 readVolatile(n){return Buffer.from(memory.subarray(this.value,this.value+n));}
 writeU32(n){memory.writeUInt32LE(n,this.value);return this;}
 readU8(){return memory[this.value];}
 readU32(){return memory.readUInt32LE(this.value);}
 writeU8(n){memory[this.value]=n;return this;}
 writePointer(p){memory.writeBigUInt64LE(BigInt(p.value),this.value);return this;}
 readPointer(){return new Pointer(Number(memory.readBigUInt64LE(this.value)));}
 writeUtf8String(s){memory.write(s+'\0',this.value);return this;}
 readUtf8String(){return memory.toString('utf8',this.value,memory.indexOf(0,this.value));}
}
const ptr=n=>new Pointer(n),alloc=n=>{const p=ptr(next);next+=n;return p;};
const signatures=[[0x13257b0,'488b05713336034885c07408'],[0x175e260,'48895c2410574883ec60ff81'],
 [0x18dc1a0,'48895c2418574883ec60488b'],[0x185ae70,'405356574883ec20488bf948'],
 [0xdfbc0,'48895c240848897424105748'],[0x19ba650,'40564883ec20'],[0x1855ae0,'40534883ec4048896c245848'],
 [0x1689780,'4883ec48488b41288b501c0f'],[0x17c3720,'40534883ec20488bcae85260'],
 [0x17a28e0,'41574883ec304c8b7a204d85'],[0x1bb9640,'4881c150020000e9c4794bfe'],
 [0x1bb9c00,'48895c240848896c24104889']];
for(const [rva,hex] of signatures)Buffer.from(hex,'hex').copy(memory,rva);
ptr(0x32e5338).writePointer(ptr(0x19ba650));
ptr(0x3217a88).writePointer(ptr(0x17c3720));ptr(0x32170c8).writePointer(ptr(0x17a28e0));
const root=ptr(0x4000000);root.add(0x28).writePointer(ptr(0x4200000));
ptr(0x4200340).writePointer(ptr(1));root.add(0x110).writePointer(ptr(1));
function setManager(value){manager=value;root.add(0x1a8).writePointer(ptr(value));
 ptr(value+0x238).writePointer(ptr(1));ptr(value+0x260).writePointer(ptr(1));}
setManager(manager);
function string(row,offset){assert.equal(row.add(offset).readPointer().value,0x2910bb8);
 assert.equal(row.add(offset+0x50).readU32(),65);return row.add(offset+0x48).readPointer().readUtf8String();}
function revision(){const p=ptr(manager+0x3f8);p.writeU32(p.readU32()+1);}
const voiceMutes=new Map(), input=alloc(32), handle=alloc(96), profile=alloc(96), ui=alloc(64), output=alloc(32);
input.writePointer(handle);input.add(0x1c).writeU32(1);handle.add(0x50).writePointer(profile);
output.add(0x1c).writeU32(1);ui.add(0x20).writePointer(output);ui.add(0x28).writePointer(input);
const context={Date:{now:()=>now},
 Memory:{alloc,protect:(p,n)=>{assert([0x32e5338,0x3217a88,0x32170c8].includes(p.value));assert.equal(n,8);return true;}},
 Process:{findRangeByAddress:()=>({protection:'r--'})},ptr,
 Interceptor:{attach:()=>assert.fail('No executable hooks'),replace:()=>assert.fail('No SDK hooks')},
 NativeCallback:function(fn){const p=alloc(8);callbacks.set(p.value,fn);return p;},
 NativeFunction:function(p){return (...args)=>{
  if(p.value===0x13257b0)return live?root:ptr(0);
  if(p.value===0x1689780)return input;
  if(p.value===0x17c3720||p.value===0x17a28e0)return;
  if(p.value===0x1bb9640)return voiceMutes.get(args[1].add(2).readUtf8String())?1:0;
  if(p.value===0x1bb9c00){voiceMutes.set(args[1].add(2).readUtf8String(),!!args[2]);return;}
  calls.push(p.value);
  if(p.value===0x19ba650)return; // original UI must run after reconcile
  assert.equal(args[0].value===manager||p.value===0xdfbc0,true);
  if(p.value===0x175e260){const row=args[1],id=string(row,0x58);
   assert.equal(string(row,0),'');assert.equal(row.add(0x108).readU32(),1);
   friends.set(id,{name:string(row,0xb0),online:false});revision();
  }else if(p.value===0x18dc1a0){const row=args[1];assert.equal(string(row,0),'');assert.equal(string(row,0x58),'');
   const status=row.add(0x108).readU32();assert([0,1].includes(status));
   friends.get(string(row,0xb0)).online=status===1;
  }else if(p.value===0xdfbc0){args[1].add(2).writeUtf8String(string(args[0],0x58));return args[1];
  }else if(p.value===0x1855ae0){assert.equal(args[2],0);assert(args[3].isNull());
   const friend=friends.get(args[1].add(2).readUtf8String());if(!friend)return ptr(0);
   const value=ptr(0x4300000);value.add(0x120).writeU32(1);value.add(0x230).writeU32(friend.online?1:0);return value;
  }else if(p.value===0x185ae70){friends.delete(args[1].add(2).readUtf8String());revision();}
  else assert.fail('Unexpected native call '+p.value.toString(16));
 };}
};
vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/private_friends_agent.js','utf8'),context);
const events=[],bridge=context.createPrivateFriendsBridge({base:ptr(0)},e=>events.push(e),new Map());
const render=()=>callbacks.get(ptr(0x32e5338).readPointer().value)(ptr(42),ptr(43));
const id='12345678-1234-1234-1234-123456789abc',entry={accountId:id,name:'Private Friend',online:true};
bridge.update([entry]);assert.equal(calls.length,0,'RPC cannot call the engine');
render();assert.deepEqual(calls,[0x19ba650]);assert.equal(friends.size,0,'wait for game services');
live=true;render();assert.deepEqual(friends.get(id),{name:entry.name,online:true});
assert.deepEqual(calls.slice(-5),[0x175e260,0x18dc1a0,0xdfbc0,0x1855ae0,0x19ba650]);
const count=calls.length;render();assert.equal(calls.length,count+1,'no duplicate unchanged updates');
assert.throws(()=>bridge.update([entry,{...entry,accountId:id.toUpperCase()}]));
assert.throws(()=>bridge.update([{...entry,name:'bad\nname'}]));
assert.throws(()=>bridge.update([{...entry,name:'x'.repeat(65)}]));
bridge.update([{...entry,name:'x'.repeat(64),online:false}]);render();
assert.deepEqual(friends.get(id),{name:'x'.repeat(64),online:false});
bridge.update([]);render();assert.equal(friends.size,0,'expiry/removal removes only projected friends');
bridge.update([entry]);render();friends.clear();revision();render();assert.equal(friends.size,1,'native clear repopulates');
setManager(0x4110000);friends.clear();render();assert.equal(friends.size,1,'new manager repopulates without old receiver');
assert(events.includes('private_friends_manager_reconciled'));
for(const [rva,hex] of signatures)assert.equal(memory.subarray(rva,rva+hex.length/2).toString('hex'),hex);
assert.equal(bridge.exception({}),false);
profile.add(0x18).writeU8(2);profile.add(0x19).writeU8(36);profile.add(0x1a).writeUtf8String(id);
const toggle=()=>callbacks.get(ptr(0x3217a88).readPointer().value)(ptr(0),ui);
const display=()=>callbacks.get(ptr(0x32170c8).readPointer().value)(ptr(0),ui);
toggle();display();assert.equal(output.readU8(),1,'mute is visible before voice service initialization');
assert.equal(voiceMutes.size,0);
root.add(0x130).writePointer(ptr(0x4001000));now+=1001;render();
assert.equal(voiceMutes.get(id),true,'pending preference applies to the actual voice manager');
toggle();display();assert.equal(output.readU8(),0);assert.equal(voiceMutes.get(id),false,'unmute reaches native voice manager');
assert(events.includes('private_friend_muted')&&events.includes('private_friend_unmuted'));

ptr(0x32e5338).writePointer(ptr(1));assert.throws(()=>context.createPrivateFriendsBridge({base:ptr(0)},()=>{},new Map()));
console.log('PASS private native Friends reconcile, presence, expiry, manager lifecycle, UI ordering, and code guards');
