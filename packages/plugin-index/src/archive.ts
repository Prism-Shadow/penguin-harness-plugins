/**
 * The deterministic archiver: one byte stream per set of files, the same bytes on every
 * machine that packs the same files.
 *
 * Two consumers depend on that. The harness's hot push packs each plugin into a `.tgz`
 * whose blob is content-addressed, so an unchanged package must not look changed. And a
 * plugin entry's `integrity` is the sha256 of this archive over the entry's `package/`
 * directory — computed by the index when a manifest is checked, by the harness when a
 * package enters a machine's plugin store, and by the push when it ships one. Three
 * places computing one value is only safe if they run one piece of code, which is why
 * the archiver is written here rather than borrowed from a tar library whose output may
 * change with its version.
 *
 * The format is a POSIX ustar stream, fully specified by this file:
 *
 *   - one entry per regular file; no directory entries (an unpacker creates parents), and
 *     symlinks are not files (npm's `.bin` shims are left out);
 *   - entries sorted by their path, comparing UTF-16 code units (JavaScript's `<`);
 *   - every mtime is 0, uid and gid are 0, user and group names are empty;
 *   - the mode is 0755 when any execute bit is set on the source file and 0644 otherwise;
 *   - a path that does not fit the ustar name and prefix fields, or is not ASCII, is
 *     carried in a pax extended header (`path=`) just before its entry;
 *   - the stream ends with two zero blocks.
 *
 * `integrity` is `sha256-<hex>` of this stream, before any compression: the bytes gzip
 * produces depend on the zlib that ran it.
 */
import { createHash } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";

/** One file of an archive: `rel` its posix path inside the archive, `abs` where it is read. */
export interface ArchiveFile {
  rel: string;
  abs: string;
  exec: boolean;
}

const BLOCK = 512;
const MAX_SIZE = 0o77777777777; // 11 octal digits: 8 GiB - 1

const byCodeUnit = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

function octal(value: number, width: number): string {
  return `${value.toString(8).padStart(width - 1, "0")}\0`;
}

function header(name: string, prefix: string, size: number, mode: number, type: string): Buffer {
  const block = Buffer.alloc(BLOCK);
  block.write(name, 0, 100, "utf8");
  block.write(octal(mode, 8), 100, 8, "ascii");
  block.write(octal(0, 8), 108, 8, "ascii"); // uid
  block.write(octal(0, 8), 116, 8, "ascii"); // gid
  block.write(octal(size, 12), 124, 12, "ascii");
  block.write(octal(0, 12), 136, 12, "ascii"); // mtime
  block.write("        ", 148, 8, "ascii"); // checksum, as spaces while summing
  block.write(type, 156, 1, "ascii");
  block.write("ustar\0", 257, 6, "ascii");
  block.write("00", 263, 2, "ascii");
  block.write(prefix, 345, 155, "utf8");
  let sum = 0;
  for (const byte of block) sum += byte;
  block.write(`${sum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");
  return block;
}

function padding(size: number): Buffer {
  const rest = size % BLOCK;
  return Buffer.alloc(rest === 0 ? 0 : BLOCK - rest);
}

/** `rel` as ustar's name and prefix fields, or null when it needs a pax header. */
function ustarPath(rel: string): { name: string; prefix: string } | null {
  if (!/^[\x20-\x7e]*$/.test(rel)) return null;
  if (rel.length <= 100) return { name: rel, prefix: "" };
  for (let i = rel.indexOf("/"); i !== -1; i = rel.indexOf("/", i + 1)) {
    const prefix = rel.slice(0, i);
    const name = rel.slice(i + 1);
    if (prefix.length <= 155 && name.length <= 100 && name !== "") return { name, prefix };
  }
  return null;
}

/** One pax record, `<length> path=<value>\n`, where the length counts itself. */
function paxRecord(key: string, value: string): Buffer {
  const body = ` ${key}=${value}\n`;
  const bodyBytes = Buffer.byteLength(body, "utf8");
  let length = bodyBytes + 1;
  while (String(length).length + bodyBytes !== length) length += 1;
  return Buffer.from(`${length}${body}`, "utf8");
}

function assertRel(rel: string): void {
  if (
    rel === "" ||
    rel.startsWith("/") ||
    rel.includes("\\") ||
    rel.includes("\0") ||
    rel.split("/").some((part) => part === "" || part === "." || part === "..")
  ) {
    throw new Error(`'${rel}' is not a relative posix path an archive may hold`);
  }
}

/**
 * The archive over `files`, as a sequence of chunks — so a caller hashing it never holds
 * more than one file in memory.
 */
export async function* archiveChunks(files: Iterable<ArchiveFile>): AsyncGenerator<Buffer> {
  const sorted = [...files].sort((a, b) => byCodeUnit(a.rel, b.rel));
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i]!.rel === sorted[i - 1]!.rel) {
      throw new Error(`'${sorted[i]!.rel}' appears twice in one archive`);
    }
  }
  for (const file of sorted) {
    assertRel(file.rel);
    const data = await fsp.readFile(file.abs);
    if (data.byteLength > MAX_SIZE) throw new Error(`${file.rel} is too large for an archive`);
    const mode = file.exec ? 0o755 : 0o644;
    const fits = ustarPath(file.rel);
    if (fits === null) {
      const pax = paxRecord("path", file.rel);
      yield header("PaxHeader", "", pax.byteLength, 0o644, "x");
      yield pax;
      yield padding(pax.byteLength);
      // The ustar name is only a fallback for readers that ignore pax: keep it ASCII.
      const fallback = file.rel.replace(/[^\x20-\x7e]/g, "_").slice(-100);
      yield header(fallback, "", data.byteLength, mode, "0");
    } else {
      yield header(fits.name, fits.prefix, data.byteLength, mode, "0");
    }
    yield data;
    yield padding(data.byteLength);
  }
  yield Buffer.alloc(BLOCK * 2);
}

/** The archive over `files`, uncompressed, as bytes. */
export async function packTar(files: Iterable<ArchiveFile>): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of archiveChunks(files)) chunks.push(chunk);
  return Buffer.concat(chunks);
}

/**
 * The archive over `files`, gzipped: what the hot push ships. Deterministic for one zlib;
 * the integrity is never computed over these bytes (see the file header).
 */
export async function packArchive(files: Iterable<ArchiveFile>): Promise<Buffer> {
  return gzipSync(await packTar(files), { level: 9 });
}

/** `sha256-<hex>` of the archive over `files`, with the archive's size in bytes. */
export async function archiveIntegrity(
  files: Iterable<ArchiveFile>,
): Promise<{ integrity: string; size: number }> {
  const hash = createHash("sha256");
  let size = 0;
  for await (const chunk of archiveChunks(files)) {
    hash.update(chunk);
    size += chunk.byteLength;
  }
  return { integrity: `sha256-${hash.digest("hex")}`, size };
}

/** Regular files under `dir`, as sorted relative posix paths; symlinks are not files. */
export async function walkFiles(dir: string, prefix = ""): Promise<string[]> {
  const out: string[] = [];
  for (const e of await fsp.readdir(dir, { withFileTypes: true })) {
    const rel = prefix === "" ? e.name : `${prefix}/${e.name}`;
    if (e.isDirectory()) out.push(...(await walkFiles(path.join(dir, e.name), rel)));
    else if (e.isFile()) out.push(rel);
  }
  return out.sort(byCodeUnit);
}

/** Whether anything may execute `abs`: any of its execute bits. */
export function isExecutable(abs: string): boolean {
  try {
    return (fs.statSync(abs).mode & 0o111) !== 0;
  } catch {
    return false;
  }
}

/** Every regular file under `dir`, placed under `under` in the archive. */
export async function filesUnder(dir: string, under: string): Promise<ArchiveFile[]> {
  return (await walkFiles(dir)).map((rel) => {
    const abs = path.join(dir, ...rel.split("/"));
    return { rel: under === "" ? rel : `${under}/${rel}`, abs, exec: isExecutable(abs) };
  });
}
