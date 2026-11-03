/**
 * The index claims things about a package it does not host: that the version exists,
 * that it is licensed as stated, that it is a PenguinHarness plugin at all, and — the
 * claim everything else rests on — that installing the entry's lock produces exactly the
 * content its `integrity` names. This module is where those claims are checked against
 * npm, the registry that actually serves the code.
 *
 * The content check is the one a machine makes before it stores the package: install
 * from the lock, archive the package with the shared archiver, hash, compare. Both run
 * `@prismshadow/penguin-plugin-index`, so the index cannot accept a value a store would
 * reject.
 *
 * The probe is deliberately STATIC. The install runs no scripts, and the entry file is
 * read, never imported. Executing a submission's code in CI would hand every contributor
 * a shell on the machine that publishes our index — the one supply-chain step this
 * repository exists to keep honest.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  installFromLock,
  packageFiles,
  archiveIntegrity,
  type Manifest,
  type NpmRunner,
} from "@prismshadow/penguin-plugin-index";
import { MAX_ENTRY_BYTES } from "./vocabulary.js";

export interface PackumentVersion {
  version: string;
  description?: string;
  license?: unknown;
  repository?: unknown;
  deprecated?: string;
  type?: string;
  main?: string;
  exports?: unknown;
}

export interface Packument {
  name?: string;
  versions?: Record<string, PackumentVersion>;
}

const REGISTRY = "https://registry.npmjs.org";

export async function fetchPackument(
  name: string,
  fetchImpl: typeof fetch = fetch,
): Promise<Packument> {
  const res = await fetchImpl(`${REGISTRY}/${encodeURIComponent(name)}`);
  if (res.status === 404) throw new Error(`package "${name}" does not exist on npm`);
  if (!res.ok) throw new Error(`npm answered HTTP ${res.status} for "${name}"`);
  return (await res.json()) as Packument;
}

/**
 * npm's `repository` is a string or `{ type, url }`, the string possibly npm's shorthand
 * (`github:owner/repo`, `owner/repo`); all normalize to a bare https URL.
 */
export function repositoryUrl(repository: unknown): string | null {
  const raw =
    typeof repository === "string"
      ? repository
      : typeof (repository as { url?: unknown })?.url === "string"
        ? ((repository as { url: string }).url as string)
        : null;
  if (raw === null) return null;
  return raw
    .replace(/^github:/, "https://github.com/")
    .replace(/^([A-Za-z0-9][\w.-]*\/[\w.-]+)$/, "https://github.com/$1")
    .replace(/^git\+/, "")
    .replace(/^git:\/\//, "https://")
    .replace(/^ssh:\/\/git@/, "https://")
    .replace(/\.git$/, "")
    .replace(/\/+$/, "");
}

/** Resolves the module the harness would import, from `exports` or `main`. */
export function entryPointOf(pkg: PackumentVersion): string | null {
  const fromExports = (value: unknown): string | null => {
    if (typeof value === "string") return value;
    if (typeof value !== "object" || value === null) return null;
    const record = value as Record<string, unknown>;
    for (const key of [".", "import", "node", "default"]) {
      if (key in record) {
        const resolved = fromExports(record[key]);
        if (resolved !== null) return resolved;
      }
    }
    return null;
  };
  const exported = fromExports(pkg.exports);
  if (exported !== null) return exported;
  if (typeof pkg.main === "string") return pkg.main;
  return "index.js";
}

const ACTIVATE_PATTERNS = [
  /export\s+(?:async\s+)?function\s+activate\b/,
  /export\s+(?:const|let|var)\s+activate\b/,
  /export\s*\{[^}]*\bactivate\b[^}]*\}/s,
  /exports\.activate\s*=/,
];

/** Whether the entry file's source declares an `activate` export. */
export function declaresActivate(source: string): boolean {
  return ACTIVATE_PATTERNS.some((pattern) => pattern.test(source));
}

/**
 * Reads an installed package's own manifest and entry file. Returns the problems found;
 * an empty array means the package looks like a loadable PenguinHarness plugin.
 */
export function probePackage(root: string): string[] {
  const manifestPath = path.join(root, "package.json");
  if (!fs.existsSync(manifestPath)) return ["the installed package has no package.json"];
  const problems: string[] = [];
  const published = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as PackumentVersion;
  if (published.type !== "module") {
    problems.push(`package.json must declare "type": "module"; the harness imports plugins as ESM`);
  }
  const entry = entryPointOf(published);
  const entryPath = path.resolve(root, entry ?? "index.js");
  if (!entryPath.startsWith(root + path.sep) || !fs.existsSync(entryPath)) {
    problems.push(`entry point "${entry}" is missing from the published package`);
    return problems;
  }
  if (!declaresActivate(fs.readFileSync(entryPath, "utf8"))) {
    problems.push(
      `entry point "${entry}" declares no "activate" export; a plugin's contract is one named export activate(ctx)`,
    );
  }
  return problems;
}

/**
 * Installs the entry from its lock into a scratch prefix and compares what it produced
 * with the manifest: the integrity, the size, and the static probe. `npm` is injectable
 * so tests never reach a registry.
 */
export async function checkContent(
  manifest: Manifest,
  lock: string,
  npm?: NpmRunner,
): Promise<string[]> {
  const prefix = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-plugin-"));
  try {
    let pkgDir: string;
    try {
      pkgDir = await installFromLock(prefix, manifest.name, manifest.version, lock, npm);
    } catch (err) {
      return [
        `installing from package-lock.json failed: ${err instanceof Error ? err.message : err}`,
      ];
    }
    if (!fs.existsSync(path.join(pkgDir, "package.json"))) {
      return [`installing from package-lock.json produced no ${manifest.name}`];
    }
    const problems: string[] = [];
    const { integrity, size } = await archiveIntegrity(await packageFiles(pkgDir, prefix));
    if (integrity !== manifest.integrity) {
      problems.push(
        `the installed package's integrity is ${integrity}, the manifest says ${manifest.integrity}`,
      );
    }
    if (size > MAX_ENTRY_BYTES) {
      problems.push(
        `the package archives to ${size} bytes, over the ${MAX_ENTRY_BYTES} byte limit`,
      );
    }
    problems.push(...probePackage(pkgDir));
    return problems;
  } finally {
    fs.rmSync(prefix, { recursive: true, force: true });
  }
}

/**
 * Checks one entry against npm. Metadata must agree exactly: the manifest is
 * hand-written, so a drift between it and the published package is either a stale entry
 * or a submission describing something other than what npm will serve.
 */
export async function checkAgainstNpm(
  manifest: Manifest,
  lock: string,
  { fetchImpl = fetch, npm }: { fetchImpl?: typeof fetch; npm?: NpmRunner } = {},
): Promise<string[]> {
  const problems: string[] = [];
  let packument: Packument;
  try {
    packument = await fetchPackument(manifest.name, fetchImpl);
  } catch (err) {
    return [err instanceof Error ? err.message : String(err)];
  }

  const pkg = packument.versions?.[manifest.version];
  if (pkg === undefined) {
    return [`version ${manifest.version} of "${manifest.name}" is not published on npm`];
  }
  if (typeof pkg.deprecated === "string") {
    problems.push(`npm marks ${manifest.name}@${manifest.version} as deprecated`);
  }
  if (pkg.license !== manifest.license) {
    problems.push(
      `license mismatch: manifest says "${manifest.license}", npm says "${String(pkg.license)}"`,
    );
  }
  if (pkg.description !== manifest.description) {
    problems.push(`description does not match the "description" in the published package.json`);
  }
  const published = repositoryUrl(pkg.repository);
  const declared = manifest.repository.replace(/\/+$/, "");
  if (published !== null && published !== declared) {
    problems.push(`repository mismatch: manifest says "${declared}", npm says "${published}"`);
  }

  problems.push(...(await checkContent(manifest, lock, npm)));
  return problems;
}
