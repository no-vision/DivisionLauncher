/* Opt-in private-client observation. Hardware breakpoint only; no actor,
 * packet, register or game-code writes. Bounded to one configured private actor,
 * 24 focus updates and 120 seconds. RVAs apply only to the checked client build.
 * See Research/RestockCleanup/focus-apply.asm for the pointer chain. */
'use strict';
function createAimDiagnostic(game, actorGuid, publish) {
  const address = game.base.add(0x15fbba0);
  const threads = new Map();
  const armedThreads = new Set();
  let active = false, count = 0, timer = null;
  function bytes(pointer, length) {
    return Array.from(new Uint8Array(pointer.readVolatile(length)), value => value.toString(16).padStart(2, '0')).join('');
  }
  function vector(pointer) {
    const values = [pointer.readFloat(), pointer.add(4).readFloat(), pointer.add(8).readFloat()];
    if (!values.every(Number.isFinite)) throw new Error('Nonfinite vector');
    return values;
  }
  function target(pointer) {
    if (pointer.isNull()) return null;
    return {parameters: vector(pointer), point: vector(pointer.add(0x20))};
  }
  function snapshot(actor) {
    if (!actor.readPointer().equals(game.base.add(0x30bb618)) || identity(actor) !== actorGuid)
      throw new Error('Actor identity changed');
    const controller = actor.add(0x4e8).readPointer().add(0x1a0).readPointer();
    return {
      position: vector(actor.add(0x80)), forward: vector(actor.add(0x60)),
      focusDirection: vector(controller.add(0x14)),
      channel1: target(controller.add(0x30)), channel0: target(controller.add(0x90)),
      activeFacing: target(controller.add(0xe0).readPointer()),
      activeLook: target(controller.add(0xe8).readPointer()),
      lookFlag: controller.add(0xd0).readU8(),
      dirty: [controller.add(0x108).readU8(), controller.add(0x109).readU8(), controller.add(0x10a).readU8()]
    };
  }
  function identity(actor) {
    const handle = actor.add(0x28).readPointer();
    return handle.isNull() ? null : bytes(handle.add(0x40), 16);
  }
  function stop() {
    if (!active) return;
    active = false;
    for (const thread of threads.values()) {
      try { thread.unsetHardwareBreakpoint(1); } catch (_) {}
    }
    if (timer !== null) clearTimeout(timer);
    publish({event: 'aim_trace_stopped', samples: count});
  }
  function added(thread) {
    threads.set(thread.id, thread);
    if (active) {
      try { thread.setHardwareBreakpoint(1, address); armedThreads.add(thread.id); } catch (_) { stop(); }
    }
  }
  function removed(thread) { threads.delete(thread.id); armedThreads.delete(thread.id); }
  function start() {
    if (!/^[a-f0-9]{32}$/.test(actorGuid) || bytes(address, 10) !== '48895c24084889742418')
      throw new Error('Aim diagnostic build or actor mismatch');
    stop(); count = 0;
    active = true;
    try {
      for (const thread of threads.values()) {
        thread.setHardwareBreakpoint(1, address);
        armedThreads.add(thread.id);
      }
    } catch (error) { stop(); throw error; }
    timer = setTimeout(stop, 120000);
    publish({event: 'aim_trace_armed'});
  }
  function exception(details) {
    if (!['single-step', 'breakpoint'].includes(details.type) || !details.context.pc.equals(address)) return false;
    const thread = threads.get(Process.getCurrentThreadId());
    if (!thread || !armedThreads.has(thread.id)) return false;
    // A different game thread can already have queued its breakpoint exception
    // when the sample limit removes all breakpoints. Consume that owned late
    // exception too; forwarding it as unhandled can terminate the process.
    // Never swallow access violations or unrelated breakpoints at this address.
    try { thread.unsetHardwareBreakpoint(1); } catch (_) { return false; }
    if (!active) return true;
    setTimeout(() => {
      if (active && threads.has(thread.id)) {
        try { thread.setHardwareBreakpoint(1, address); } catch (_) { stop(); }
      }
    }, 100);
    try {
      const packet = details.context.r8;
      if (bytes(packet.add(0x20), 16) !== actorGuid) return true;
      const actor = details.context.rdx, sequence = ++count;
      publish({event: 'aim_trace', sequence, phase: 'before', channel: packet.add(0x30).readU8(),
        kind: packet.add(0x31).readU8(), wirePoint: vector(packet.add(0x48)), state: snapshot(actor)});
      // Delay only observational reads, never the game thread. Stop sampling if
      // the actor changes position/type/lifetime: no cached pointer dereference
      // is trusted after the next 250ms without checking its original identity.
      setTimeout(() => {
        try {
          publish({event: 'aim_trace', sequence, phase: 'after', state: snapshot(actor)});
        } catch (_) { publish({event: 'aim_trace_unavailable', sequence}); }
      }, 250);
      if (count >= 24) stop();
    } catch (_) { publish({event: 'aim_trace_unavailable'}); }
    return true;
  }
  return {start, stop, added, removed, exception};
}
