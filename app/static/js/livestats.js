// Running statistics for a live session (kept in memory only; nothing is stored or sent).
export const LIVE_GAP_SECONDS = 30;

export function zoneIndex(bpm, zones) {
  let idx = -1;
  zones.forEach((z, i) => { if (bpm >= z.min) idx = i; });
  return idx;
}

export class LiveStats {
  constructor(zones) {
    this.zones = zones;
    this.reset();
  }
  reset() {
    this.t = [];          // seconds since session start
    this.bpm = [];        // null marks a gap (disconnect)
    this.sum = 0; this.n = 0; this.max = null;
    this.tiz = { below: 0 };
    for (const z of this.zones) this.tiz[z.type] = 0;
    this.last = null;     // { t, bpm }
  }
  /** Add one reading at `t` seconds. Time since the previous reading counts toward the previous reading's zone. */
  add(t, bpm) {
    if (this.last && this.last.bpm != null) {
      const dt = t - this.last.t;
      if (dt > 0 && dt <= LIVE_GAP_SECONDS) {
        const i = zoneIndex(this.last.bpm, this.zones);
        this.tiz[i < 0 ? "below" : this.zones[i].type] += dt;
      }
    }
    this.t.push(t); this.bpm.push(bpm);
    this.sum += bpm; this.n++;
    this.max = this.max == null ? bpm : Math.max(this.max, bpm);
    this.last = { t, bpm };
  }
  /** Break the line (e.g. on disconnect). */
  gap(t) {
    if (this.bpm.length && this.bpm[this.bpm.length - 1] !== null) { this.t.push(t); this.bpm.push(null); }
    this.last = null;
  }
  get avg() { return this.n ? Math.round(this.sum / this.n) : null; }
}
