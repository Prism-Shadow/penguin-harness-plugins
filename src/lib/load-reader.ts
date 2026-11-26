/**
 * Loading a PenguinHarness plugin-index reader to run: the `parsePluginIndex` a published release
 * ships, or the one a harness checkout carries in source.
 *
 * A published reader is the release's own bytes. The version's tarball is fetched from the
 * registry and checked against the registry's `dist.integrity` before anything in it runs,
 * unpacked into a scratch directory, and imported through the package's own `exports` entry —
 * the file a deployment of that release imports. Nothing is installed: the reader is pure (it
 * imports nothing), and one that grows a dependency fails to load here and says so, rather than
 * being judged by something that is not the release's code.
 */
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";
import semver from "semver";
import { fetchPackument, type Packument } from "./npm.js";

const execFileAsync = promisify(execFile);

const FETCH_TIMEOUT_MS = 60_000;

/** The reader's signature, as every release so far exports it. */
export type ParsePluginIndex = (
  data: unknown,
  source: string,
  options?: { icons?: "keep" | "drop" },
) => unknown;

/** One reader to judge the document with. */
export interface Reader {
  /** Who is reading, for every line that reports on it: `@prismshadow/penguin-core@0.2.19`. */
  label: string;
  parse: ParsePluginIndex;
}

/** A reader the gate could not load: the gate cannot vouch for a reader it cannot run. */
export interface LoadFailure {
  label: string;
  error: string;
}

/** The versions of a package published from `floor` up, oldest first. */
export function versionsFrom(packument: Packument, floor: string): string[] {
  return Object.keys(packument.versions ?? {})
    .filter((version) => semver.valid(version) !== null && semver.gte(version, floor))
    .sort(semver.compare);
}

/**
 * The file an `exports` entry resolves to for an ESM import under Node: a string target, or the
 * first of an object's keys (in its own order, as Node walks it) that is a condition Node matches.
 */
function exportTarget(entry: unknown): string | null {
  if (typeof entry === "string") return entry;
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)) return null;
  for (const [condition, target] of Object.entries(entry)) {
    if (condition === "node" || condition === "import" || condition === "default") {
      const resolved = exportTarget(target);
      if (resolved !== null) return resolved;
    }
  }
  return null;
}

/** Imports `file` and answers its `parsePluginIndex`, or throws naming what is missing. */
async function importReader(file: string): Promise<ParsePluginIndex> {
  const module = (await import(pathToFileURL(file).href)) as { parsePluginIndex?: unknown };
  if (typeof module.parsePluginIndex !== "function") {
    throw new Error(`${file} exports no parsePluginIndex function`);
  }
  return module.parsePluginIndex as ParsePluginIndex;
}

/** The reader a harness checkout carries in source (a `.ts` file needs a loader that runs it, as tsx does). */
export async function sourceReader(label: string, file: string): Promise<Reader> {
  return { label, parse: await importReader(path.resolve(file)) };
}

/** Downloads, verifies and unpacks one published version, and imports its reader. */
async function publishedReader(
  pkg: string,
  exportPath: string,
  version: string,
  packument: Packument,
  workDir: string,
  fetchImpl: typeof fetch,
): Promise<Reader> {
  const label = `${pkg}@${version}`;
  const dist = packument.versions?.[version]?.dist;
  if (typeof dist?.tarball !== "string" || typeof dist.integrity !== "string") {
    throw new Error(`the registry names no tarball and sha512 integrity for ${label}`);
  }
  const res = await fetchImpl(dist.tarball, { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
  if (!res.ok) throw new Error(`${dist.tarball} answered HTTP ${res.status}`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  const served = `sha512-${createHash("sha512").update(bytes).digest("base64")}`;
  if (served !== dist.integrity) {
    throw new Error(`the tarball's hash is ${served}, the registry pins ${dist.integrity}`);
  }

  // Relative names and a cwd, as assets.ts unpacks: a Windows path's drive letter reads to GNU
  // tar as a remote host.
  const dir = path.join(workDir, version);
  await fsp.mkdir(dir, { recursive: true });
  await fsp.writeFile(path.join(dir, "reader.tgz"), bytes);
  await execFileAsync("tar", ["-xzf", "reader.tgz", "package"], { cwd: dir });
  const root = path.join(dir, "package");

  const manifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8")) as {
    exports?: Record<string, unknown>;
  };
  const target = exportTarget(manifest.exports?.[exportPath]);
  if (target === null) throw new Error(`${label} has no "${exportPath}" export`);
  const file = path.resolve(root, target);
  if (!file.startsWith(root + path.sep)) {
    throw new Error(`${label}'s "${exportPath}" export points outside the package`);
  }
  return { label, parse: await importReader(file) };
}

/**
 * The reader of every version of `pkg` published from `floor` up, oldest first, each imported
 * through its `exportPath` export. A version that will not load is a failure in its own right —
 * the gate cannot vouch for it — and does not stop the others from loading.
 */
export async function publishedReaders(
  pkg: string,
  exportPath: string,
  floor: string,
  workDir: string,
  { fetchImpl = fetch }: { fetchImpl?: typeof fetch } = {},
): Promise<{ readers: Reader[]; failures: LoadFailure[] }> {
  let packument: Packument;
  try {
    packument = await fetchPackument(pkg, fetchImpl);
  } catch (err) {
    return {
      readers: [],
      failures: [{ label: `${pkg}@>=${floor}`, error: errorText(err) }],
    };
  }
  const versions = versionsFrom(packument, floor);
  if (versions.length === 0) {
    return {
      readers: [],
      failures: [
        { label: `${pkg}@>=${floor}`, error: `the registry lists no version from ${floor} up` },
      ],
    };
  }
  const readers: Reader[] = [];
  const failures: LoadFailure[] = [];
  for (const version of versions) {
    try {
      readers.push(await publishedReader(pkg, exportPath, version, packument, workDir, fetchImpl));
    } catch (err) {
      failures.push({ label: `${pkg}@${version}`, error: errorText(err) });
    }
  }
  return { readers, failures };
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
