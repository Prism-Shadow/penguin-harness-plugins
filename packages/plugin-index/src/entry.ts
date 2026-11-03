/**
 * An entry: one package at one version with one content, the unit both trees are made of.
 *
 * The index repository's `plugins/` subtree and a machine's plugin store
 * (`<data root>/plugin-store/`) have one shape. An entry lives at
 *
 *   <npm name>/<version>/<first 16 hex digits of its integrity>/
 *
 * and holds `manifest.toml` (the index manifest, `integrity` required) and
 * `package-lock.json` (the lock its package is installed by). The store adds `package/`
 * (the installed package, every dependency inside its own `node_modules`) and `.stored`;
 * the repository ignores those two, so the repository is a store without package data and
 * a store is a checkout of it with the data filled in.
 *
 * The path spells the entry: its name, its version and its content. Two versions of one
 * name, or two contents of one version, are two entries.
 */
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { archiveIntegrity, filesUnder, type ArchiveFile } from "./archive.js";

export const MANIFEST_FILE = "manifest.toml";
export const LOCK_FILE = "package-lock.json";
export const PACKAGE_DIR = "package";
export const STORED_FILE = ".stored";

/** `sha256-<64 lowercase hex digits>`: an entry's integrity. */
export const INTEGRITY = /^sha256-([0-9a-f]{64})$/;
/** How many hex digits of the integrity name an entry's directory. */
export const KEY_LENGTH = 16;

const SCOPED_NAME = /^@[a-z0-9][a-z0-9-._]*\/[a-z0-9][a-z0-9-._]*$/;
const PLAIN_NAME = /^[a-z0-9][a-z0-9-._]*$/;

/** Whether `name` is a valid npm package name, scoped or not. */
export function isPackageName(name: string): boolean {
  if (name.length > 214) return false;
  return name.startsWith("@") ? SCOPED_NAME.test(name) : PLAIN_NAME.test(name);
}

/** The directory key of an integrity: its first 16 hex digits, or null when malformed. */
export function entryKey(integrity: string): string | null {
  return INTEGRITY.exec(integrity)?.[1]?.slice(0, KEY_LENGTH) ?? null;
}

/** An entry's directory, relative to the tree root: `<name>/<version>/<key>`. */
export function entryDir(name: string, version: string, integrity: string): string {
  const key = entryKey(integrity);
  if (key === null) throw new Error(`'${integrity}' is not a sha256 integrity`);
  return `${name}/${version}/${key}`;
}

/** Where an entry's parts sit, as read off a path relative to the tree root. */
export interface EntryPath {
  name: string;
  version: string;
  key: string;
  /** The path inside the entry: `manifest.toml`, `package-lock.json`, `package/…`. */
  file: string;
}

/**
 * Reads `<name>/<version>/<key>/<file…>` (posix, relative to the tree root). Null when
 * the path is too short to name an entry and a file in it. The segments are not
 * validated here — that is the manifest check's job, which reports what is wrong.
 */
export function parseEntryPath(rel: string): EntryPath | null {
  const parts = rel.split("/");
  const nameParts = parts[0]?.startsWith("@") ? 2 : 1;
  if (parts.length < nameParts + 3) return null;
  return {
    name: parts.slice(0, nameParts).join("/"),
    version: parts[nameParts]!,
    key: parts[nameParts + 1]!,
    file: parts.slice(nameParts + 2).join("/"),
  };
}

interface PackageJson {
  dependencies?: unknown;
  optionalDependencies?: unknown;
}

function readPackageJson(dir: string): PackageJson | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8")) as PackageJson;
  } catch {
    return null;
  }
}

const names = (table: unknown): string[] =>
  table !== null && typeof table === "object" ? Object.keys(table) : [];

/**
 * The dependencies of the package at `pkgDir` that live OUTSIDE it, in the npm prefix
 * `prefixDir` it was installed into: what npm hoisted to `<prefix>/node_modules/<dep>`,
 * found the way Node finds a package (`node_modules` upward, stopping at the prefix), for
 * its dependencies and optional dependencies, transitively. A dependency nested inside
 * the package already travels with it; an optional one npm did not install is skipped.
 */
export function hoistedDependencies(pkgDir: string, prefixDir: string): Map<string, string> {
  const out = new Map<string, string>();
  const own = path.resolve(pkgDir);
  const top = path.resolve(prefixDir);
  const inside = (dir: string) => dir === own || dir.startsWith(own + path.sep);
  const visit = (from: string) => {
    const manifest = readPackageJson(from);
    if (manifest === null) return;
    for (const dep of [...names(manifest.dependencies), ...names(manifest.optionalDependencies)]) {
      let found: string | null = null;
      for (let dir = from; ; dir = path.dirname(dir)) {
        const candidate = path.join(dir, "node_modules", ...dep.split("/"));
        if (fs.existsSync(path.join(candidate, "package.json"))) {
          found = candidate;
          break;
        }
        if (dir === top || path.dirname(dir) === dir) break;
      }
      if (found === null || inside(found) || out.has(dep)) continue;
      out.set(dep, found);
      visit(found);
    }
  };
  visit(own);
  return out;
}

/**
 * The files of an entry's `package/`: the package at `pkgDir` and, under its own
 * `node_modules`, every dependency npm hoisted out of it into `prefixDir`. The paths
 * start with `package/`, as the integrity archives them.
 */
export async function packageFiles(pkgDir: string, prefixDir: string): Promise<ArchiveFile[]> {
  const files = await filesUnder(pkgDir, PACKAGE_DIR);
  const taken = new Set(files.map((f) => f.rel));
  for (const [dep, dir] of hoistedDependencies(pkgDir, prefixDir)) {
    for (const file of await filesUnder(dir, `${PACKAGE_DIR}/node_modules/${dep}`)) {
      // A dependency already nested inside the package wins, as Node would resolve it.
      if (!taken.has(file.rel)) {
        taken.add(file.rel);
        files.push(file);
      }
    }
  }
  if (files.length === 0) throw new Error(`${pkgDir}: the package has no files`);
  return files;
}

/**
 * The integrity of a package installed at `pkgDir` inside the npm prefix `prefixDir`
 * (`prefixDir` defaults to the package itself: an entry's `package/`, which holds all of
 * its dependencies already), with the size of the archive it is computed over.
 */
export async function packageIntegrity(
  pkgDir: string,
  prefixDir: string = pkgDir,
): Promise<{ integrity: string; size: number }> {
  return archiveIntegrity(await packageFiles(pkgDir, prefixDir));
}

/**
 * Writes `packageFiles` into the entry directory `entryDir` (so they land under its
 * `package/`), each with the mode the archive gives it.
 */
export async function copyPackage(files: readonly ArchiveFile[], entryDir: string): Promise<void> {
  for (const file of files) {
    const target = path.join(entryDir, ...file.rel.split("/"));
    await fsp.mkdir(path.dirname(target), { recursive: true });
    await fsp.copyFile(file.abs, target);
    await fsp.chmod(target, file.exec ? 0o755 : 0o644);
  }
}
