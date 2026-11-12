'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/aim_diagnostic.js', 'utf8');
const actorGuid = 'ab'.repeat(16), memory = new Map(), raw = new Map();
const callbacks = [], events = [], breakpoints = new Set();
class Pointer {
  constructor(value) { this.value = value; }
  add(value) { return new Pointer(this.value + value); }
  equals(other) { return this.value === other.value; }
  isNull() { return this.value === 0; }
  readPointer() { return new Pointer(memory.get(this.value) || 0); }
  readFloat() { return memory.get(this.value) || 0; }
  readU8() { return memory.get(this.value) || 0; }
  readVolatile(count) { return Buffer.from(raw.get(this.value) || '00'.repeat(count), 'hex'); }
}
const thread = {
  id: 1,
  setHardwareBreakpoint(slot) { assert.equal(slot, 1); breakpoints.add(slot); },
  unsetHardwareBreakpoint(slot) { breakpoints.delete(slot); }
};
const context = {
  Uint8Array, Process: {getCurrentThreadId: () => 1},
  setTimeout(callback, milliseconds) { callbacks.push({callback, milliseconds}); return callbacks.length; },
  clearTimeout() {}
};
vm.createContext(context); vm.runInContext(source, context);
const diagnostic = context.createAimDiagnostic({base: new Pointer(0)}, actorGuid, event => events.push(event));
diagnostic.added(thread);
assert.throws(() => diagnostic.start());
assert.equal(breakpoints.size, 0);
raw.set(0x15fbba0, '48895c24084889742418');
diagnostic.start();
assert.equal(breakpoints.size, 1);
const actor = 0x100000, handle = 0x200000, controller = 0x300000, packet = 0x400000;
memory.set(actor, 0x30bb618); memory.set(actor + 0x28, handle); raw.set(handle + 0x40, actorGuid);
memory.set(actor + 0x4e8, 0x500000); memory.set(0x5001a0, controller);
raw.set(packet + 0x20, actorGuid); memory.set(packet + 0x30, 1); memory.set(packet + 0x31, 2);
const registers = {pc: new Pointer(0x15fbba0), rdx: new Pointer(actor), r8: new Pointer(packet)};
const before = Object.values(registers).map(value => value.value);
assert.equal(diagnostic.exception({type: 'single-step', context: registers}), true);
assert.deepEqual(Object.values(registers).map(value => value.value), before);
assert.equal(events.filter(event => event.event === 'aim_trace').length, 1);
assert.equal(breakpoints.size, 0);
callbacks.find(timer => timer.milliseconds === 100).callback();
assert.equal(breakpoints.size, 1);
// A retired actor must never be interpreted as the old controller.
memory.set(actor, 0xdeadbeef);
callbacks.find(timer => timer.milliseconds === 250).callback();
assert.equal(events.at(-1).event, 'aim_trace_unavailable');
diagnostic.stop();
callbacks.find(timer => timer.milliseconds === 100).callback();
assert.equal(breakpoints.size, 0);
const eventCount = events.length;
assert.equal(diagnostic.exception({type: 'single-step', context: registers}), true);
assert.equal(events.length, eventCount);
assert.equal(diagnostic.exception({type: 'access-violation', context: registers}), false);
assert.equal(diagnostic.exception({type: 'single-step', context: {...registers, pc: new Pointer(1)}}), false);
diagnostic.removed(thread);
assert.equal(diagnostic.exception({type: 'single-step', context: registers}), false);
console.log('PASS: aim diagnostic build guard, registers, actor retirement, cleanup and owned late exceptions');
