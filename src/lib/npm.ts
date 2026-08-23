/**
 * The index claims things about a package it does not host: that the version exists,
 * that it is licensed as stated, that it is a PenguinHarness plugin at all. This
 * module is where those claims are checked against npm, the registry that actually
 * serves the code.
 *
 * The probe is deliberately STATIC. We download and unpack the published tarball and
 * read its entry file; we never import it. Executing a submission's code in CI would
 * hand every contributor a shell on the machine that publishes our index — the one
 * supply-chain step this repository exists to keep honest.
 */
import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Manifest } from "./manifest.js";
import { MAX_TARBALL_BYTES } from "./vocabulary.js";

export interface PackumentVersion {
  version: string;
  description?: string;
  license?: unknown;
  repository?: unknown;
  deprecated?: string;
  type?: string;
  main?: string;
  exports?: unknown;
  dist?: { tarball?: string; integrity?: string; shasum?: string; unpackedSize?: number };
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

/** npm's `repository` is a string or `{ type, url }`; both normalize to a bare https URL. */
function repositoryUrl(repository: unknown): string | null {
  const raw =
    typeof repository === "string"
      ? repository
      : typeof (repository as { url?: unknown })?.url === "string"
        ? ((repository as { url: string }).url as string)
        : null;
  if (raw === null) return null;
  return raw
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

function verifyIntegrity(buffer: Buffer, dist: PackumentVersion["dist"]): string | null {
  const integrity = dist?.integrity;
  if (typeof integrity === "string") {
    const [algorithm, expected] = integrity.split("-", 2);
    if (algorithm !== undefined && expected !== undefined) {
      const actual = crypto.createHash(algorithm).update(buffer).digest("base64");
      return actual === expected ? null : `tarball does not match its ${algorithm} integrity hash`;
    }
  }
  if (typeof dist?.shasum === "string") {
    const actual = crypto.createHash("sha1").update(buffer).digest("hex");
    return actual === dist.shasum ? null : "tarball does not match its shasum";
  }
  return "npm published no integrity hash for this version";
}

/**
 * Downloads the version's tarball and reads its entry file. Returns the problems found;
 * an empty array means the package looks like a loadable PenguinHarness plugin.
 */
export async function probeTarball(
  pkg: PackumentVersion,
  fetchImpl: typeof fetch = fetch,
): Promise<string[]> {
  const url = pkg.dist?.tarball;
  if (typeof url !== "string") return ["npm published no tarball URL for this version"];

  const res = await fetchImpl(url);
  if (!res.ok) return [`tarball download answered HTTP ${res.status}`];
  const buffer = Buffer.from(await res.arrayBuffer());
  if (buffer.byteLength > MAX_TARBALL_BYTES) {
    return [`tarball is ${buffer.byteLength} bytes, over the ${MAX_TARBALL_BYTES} byte limit`];
  }

  const problems: string[] = [];
  const integrity = verifyIntegrity(buffer, pkg.dist);
  if (integrity !== null) problems.push(integrity);

  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-plugin-"));
  try {
    const archive = path.join(dir, "package.tgz");
    fs.writeFileSync(archive, buffer);
    execFileSync("tar", ["-xzf", archive, "-C", dir], { stdio: "pipe" });

    const root = path.join(dir, "package");
    const manifestPath = path.join(root, "package.json");
    if (!fs.existsSync(manifestPath)) {
      problems.push("tarball has no package/package.json");
      return problems;
    }
    const published = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as PackumentVersion;
    if (published.type !== "module") {
      problems.push(
        `package.json must declare "type": "module"; the harness imports plugins as ESM`,
      );
    }

    const entry = entryPointOf(published);
    const entryPath = path.join(root, entry ?? "index.js");
    if (!entryPath.startsWith(root + path.sep) || !fs.existsSync(entryPath)) {
      problems.push(`entry point "${entry}" is missing from the published tarball`);
      return problems;
    }
    if (!declaresActivate(fs.readFileSync(entryPath, "utf8"))) {
      problems.push(
        `entry point "${entry}" declares no "activate" export; a plugin's contract is one named export activate(ctx)`,
      );
    }
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  return problems;
}

/**
 * Checks one manifest against npm. Metadata must agree exactly: the manifest is
 * hand-written, so a drift between it and the published package is either a stale entry
 * or a submission describing something other than what npm will serve.
 */
export async function checkAgainstNpm(
  manifest: Manifest,
  fetchImpl: typeof fetch = fetch,
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

  problems.push(...(await probeTarball(pkg, fetchImpl)));
  return problems;
}
