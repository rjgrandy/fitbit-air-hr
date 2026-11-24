import { test } from "node:test";
import assert from "node:assert/strict";
import { LiveStats, zoneIndex } from "../../app/static/js/livestats.js";

const Z = [{ type: "LIGHT", min: 30, max: 108 }, { type: "MODERATE", min: 109, max: 131 },
           { type: "VIGOROUS", min: 132, max: 161 }, { type: "PEAK", min: 162, max: 220 }];

test("zoneIndex boundaries", () => {
  assert.equal(zoneIndex(29, Z), -1);
  assert.equal(zoneIndex(108, Z), 0);
  assert.equal(zoneIndex(109, Z), 1);
  assert.equal(zoneIndex(250, Z), 3);
});

test("time in zone, avg and max accumulate per reading", () => {
  const s = new LiveStats(Z);
  [[0, 100], [1, 100], [2, 120], [4, 140], [5, 170], [6, 170]].forEach(([t, b]) => s.add(t, b));
  assert.deepEqual(s.tiz, { below: 0, LIGHT: 2, MODERATE: 2, VIGOROUS: 1, PEAK: 1 });
  assert.equal(s.max, 170);
  assert.equal(s.avg, Math.round((100 + 100 + 120 + 140 + 170 + 170) / 6));
});

test("a disconnect gap isn't counted and breaks the line", () => {
  const s = new LiveStats(Z);
  s.add(0, 100); s.add(1, 100); s.gap(2); s.add(50, 150); s.add(51, 150);
  assert.equal(s.tiz.LIGHT, 1);
  assert.equal(s.tiz.VIGOROUS, 1);
  assert.deepEqual(s.bpm, [100, 100, null, 150, 150]);
});

test("long silence without an explicit gap isn't counted", () => {
  const s = new LiveStats(Z);
  s.add(0, 100); s.add(100, 100);
  assert.equal(s.tiz.LIGHT, 0);
});
