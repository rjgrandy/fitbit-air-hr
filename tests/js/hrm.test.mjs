import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHeartRateMeasurement as p } from "../../app/static/js/hrm.js";

const dv = (...bytes) => new DataView(Uint8Array.from(bytes).buffer);

test("uint8 bpm, no extras", () => {
  assert.deepEqual(p(dv(0x00, 72)), {
    bpm: 72, contactSupported: false, contactDetected: null, energyExpendedKj: null, rrMs: [],
  });
});

test("uint16 bpm (little-endian)", () => {
  assert.equal(p(dv(0x01, 0x2c, 0x01)).bpm, 300);
});

test("sensor contact supported + detected / not detected", () => {
  const a = p(dv(0x06, 80));
  assert.equal(a.contactSupported, true);
  assert.equal(a.contactDetected, true);
  const b = p(dv(0x04, 80));
  assert.equal(b.contactDetected, false);
});

test("RR intervals in 1/1024 s converted to ms", () => {
  // flags: RR present; bpm 60; RR 1024 (=1000 ms), 512 (=500 ms)
  const r = p(dv(0x10, 60, 0x00, 0x04, 0x00, 0x02));
  assert.deepEqual(r.rrMs, [1000, 500]);
});

test("uint16 bpm + energy expended + RR", () => {
  // flags 0x19: uint16, energy, RR
  const r = p(dv(0x19, 0x96, 0x00, 0x10, 0x00, 0x00, 0x03));
  assert.equal(r.bpm, 150);
  assert.equal(r.energyExpendedKj, 16);
  assert.deepEqual(r.rrMs, [750]);
});

test("odd trailing byte is ignored, not a crash", () => {
  assert.deepEqual(p(dv(0x10, 60, 0x00, 0x04, 0x01)).rrMs, [1000]);
});

test("too-short packets throw", () => {
  assert.throws(() => p(dv(0x00)));
  assert.throws(() => p(dv(0x01, 0x50)));
});
