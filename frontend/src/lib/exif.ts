/**
 * Minimal EXIF writer for photos taken on the Capture page.
 *
 * A canvas JPEG has no metadata, so a photo saved to the camera roll would sort by the time it
 * was saved and the server would date it by upload time. This puts in the few tags that matter:
 * DateTime / DateTimeOriginal / DateTimeDigitized (+ OffsetTime*), Orientation = 1 (the pixels
 * are already upright), Make/Model/Software. Big-endian TIFF inside an APP1 segment right after
 * the JPEG's SOI marker. If the JPEG already has EXIF (e.g. ImageCapture on Android), it is kept.
 */

type Tag = { tag: number; type: 2 | 3 | 4; value: string | number };

const ASCII = 2;
const SHORT = 3;
const LONG = 4;

function pad(n: number, w = 2) {
  return String(n).padStart(w, "0");
}

/** "YYYY:MM:DD HH:MM:SS" in the phone's local time, as cameras write it. */
export function exifDate(d: Date): string {
  return `${d.getFullYear()}:${pad(d.getMonth() + 1)}:${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** "+HH:MM" / "-HH:MM" for d's local offset. */
export function exifOffset(d: Date): string {
  const m = -d.getTimezoneOffset();
  const sign = m >= 0 ? "+" : "-";
  const a = Math.abs(m);
  return `${sign}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}

function ifdSize(tags: Tag[], extra: (t: Tag) => number) {
  return 2 + tags.length * 12 + 4 + tags.reduce((n, t) => n + extra(t), 0);
}

function asciiBytes(s: string): number[] {
  const out: number[] = [];
  for (const ch of s) out.push(ch.charCodeAt(0) & 0x7f);
  out.push(0);
  return out;
}

/** Bytes of a value that doesn't fit in the 4-byte slot (only ASCII > 3 chars here). */
const overflow = (t: Tag) => (t.type === ASCII ? (() => {
  const n = asciiBytes(String(t.value)).length;
  return n > 4 ? n + (n % 2) : 0;
})() : 0);

function writeIfd(view: DataView, bytes: Uint8Array, at: number, tags: Tag[], next: number): number {
  // returns the end offset (after the overflow data); offsets are relative to the TIFF header
  tags.sort((a, b) => a.tag - b.tag);
  view.setUint16(at, tags.length);
  let dataAt = at + 2 + tags.length * 12 + 4;
  tags.forEach((t, i) => {
    const e = at + 2 + i * 12;
    view.setUint16(e, t.tag);
    view.setUint16(e + 2, t.type);
    if (t.type === ASCII) {
      const b = asciiBytes(String(t.value));
      view.setUint32(e + 4, b.length);
      if (b.length <= 4) bytes.set(b, e + 8);
      else {
        view.setUint32(e + 8, dataAt);
        bytes.set(b, dataAt);
        dataAt += b.length + (b.length % 2);
      }
    } else if (t.type === SHORT) {
      view.setUint32(e + 4, 1);
      view.setUint16(e + 8, Number(t.value));
    } else {
      view.setUint32(e + 4, 1);
      view.setUint32(e + 8, Number(t.value));
    }
  });
  view.setUint32(at + 2 + tags.length * 12, next);
  return dataAt;
}

/** The APP1 "Exif" segment for a photo taken at `when`. */
export function buildExif(when: Date, model = "Capture page"): Uint8Array {
  const date = exifDate(when);
  const off = exifOffset(when);
  const ifd0: Tag[] = [
    { tag: 0x010f, type: ASCII, value: "PhysiqueLapse" }, // Make
    { tag: 0x0110, type: ASCII, value: model }, // Model
    { tag: 0x0112, type: SHORT, value: 1 }, // Orientation: upright
    { tag: 0x0131, type: ASCII, value: "PhysiqueLapse" }, // Software
    { tag: 0x0132, type: ASCII, value: date }, // DateTime
    { tag: 0x8769, type: LONG, value: 0 }, // ExifIFD pointer (filled below)
  ];
  const exif: Tag[] = [
    { tag: 0x9003, type: ASCII, value: date }, // DateTimeOriginal
    { tag: 0x9004, type: ASCII, value: date }, // DateTimeDigitized
    { tag: 0x9010, type: ASCII, value: off }, // OffsetTime
    { tag: 0x9011, type: ASCII, value: off }, // OffsetTimeOriginal
    { tag: 0x9012, type: ASCII, value: off }, // OffsetTimeDigitized
  ];
  const tiffLen = 8 + ifdSize(ifd0, overflow) + ifdSize(exif, overflow);
  const seg = new Uint8Array(2 + 2 + 6 + tiffLen); // marker, length, "Exif\0\0", TIFF
  const v = new DataView(seg.buffer);
  v.setUint16(0, 0xffe1);
  v.setUint16(2, seg.length - 2);
  seg.set([0x45, 0x78, 0x69, 0x66, 0, 0], 4);
  const tiff = new Uint8Array(seg.buffer, 10);
  const tv = new DataView(seg.buffer, 10);
  tv.setUint16(0, 0x4d4d); // "MM": big-endian
  tv.setUint16(2, 42);
  tv.setUint32(4, 8); // IFD0 at 8
  const exifAt = 8 + ifdSize(ifd0, overflow);
  ifd0.find((t) => t.tag === 0x8769)!.value = exifAt;
  writeIfd(tv, tiff, 8, ifd0, 0);
  writeIfd(tv, tiff, exifAt, exif, 0);
  return seg;
}

function hasExif(b: Uint8Array): boolean {
  // walk the segments before the image data
  let i = 2;
  while (i + 4 < b.length && b[i] === 0xff) {
    const marker = b[i + 1];
    const len = (b[i + 2] << 8) | b[i + 3];
    if (marker === 0xe1 && b[i + 4] === 0x45 && b[i + 5] === 0x78 && b[i + 6] === 0x69 && b[i + 7] === 0x66) return true;
    if (marker === 0xda) break; // start of scan
    i += 2 + len;
  }
  return false;
}

/** JPEG bytes with EXIF for `when` (unchanged if the JPEG already has EXIF). */
export function withExif(jpeg: Uint8Array, when: Date, model?: string): Uint8Array {
  if (jpeg[0] !== 0xff || jpeg[1] !== 0xd8) throw new Error("not a JPEG");
  if (hasExif(jpeg)) return jpeg;
  // drop a JFIF APP0 right after SOI (EXIF readers expect APP1 first)
  let rest = jpeg.subarray(2);
  if (rest[0] === 0xff && rest[1] === 0xe0) rest = rest.subarray(2 + ((rest[2] << 8) | rest[3]));
  const app1 = buildExif(when, model);
  const out = new Uint8Array(2 + app1.length + rest.length);
  out.set([0xff, 0xd8], 0);
  out.set(app1, 2);
  out.set(rest, 2 + app1.length);
  return out;
}
