// Parser for the standard BLE Heart Rate Measurement characteristic (0x2A37).
// Spec: Bluetooth SIG Heart Rate Service 1.0, section 3.1.
//
// Flags byte:
//   bit 0    heart-rate value format: 0 = uint8, 1 = uint16 (little-endian)
//   bits 1-2 sensor contact status: bit 2 = feature supported, bit 1 = contact detected
//   bit 3    energy expended present (uint16, kJ)
//   bit 4    RR intervals present (one or more uint16, units of 1/1024 s)

/**
 * @param {DataView} dv  characteristic value
 * @returns {{bpm:number, contactSupported:boolean, contactDetected:boolean|null,
 *            energyExpendedKj:number|null, rrMs:number[]}}
 */
export function parseHeartRateMeasurement(dv) {
  if (!dv || dv.byteLength < 2) throw new Error("HRM packet too short");
  const flags = dv.getUint8(0);
  const is16 = (flags & 0x01) !== 0;
  const contactSupported = (flags & 0x04) !== 0;
  const contactDetected = contactSupported ? (flags & 0x02) !== 0 : null;
  const hasEnergy = (flags & 0x08) !== 0;
  const hasRR = (flags & 0x10) !== 0;

  let i = 1;
  let bpm;
  if (is16) {
    if (dv.byteLength < 3) throw new Error("HRM packet too short for uint16 bpm");
    bpm = dv.getUint16(i, true);
    i += 2;
  } else {
    bpm = dv.getUint8(i);
    i += 1;
  }

  let energyExpendedKj = null;
  if (hasEnergy && i + 2 <= dv.byteLength) {
    energyExpendedKj = dv.getUint16(i, true);
    i += 2;
  }

  const rrMs = [];
  if (hasRR) {
    for (; i + 2 <= dv.byteLength; i += 2) {
      rrMs.push(Math.round((dv.getUint16(i, true) / 1024) * 1000));
    }
  }
  return { bpm, contactSupported, contactDetected, energyExpendedKj, rrMs };
}
