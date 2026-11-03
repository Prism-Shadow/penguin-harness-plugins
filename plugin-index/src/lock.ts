/**
 * An entry's `package-lock.json`, and the one way a package is installed from it.
 *
 * The lock is what makes an entry's content reproducible from the registry: npm installs
 * exactly the tree it names, each tarball checked against the lock's own integrity. The
 * install is fixed here, flags and all, because the integrity is computed over what it
 * produces — a second installer with other flags (another layout, platform packages
 * skipped) would produce another directory and another hash.
 *
 *   - a prefix of its own: `package.json` naming the one package at its exact version,
 *     the lock beside it, `npm ci`;
 *   - `--ignore-scripts`: nothing the package ships runs while it is installed;
 *   - `--omit=dev`: what the package runs, not what it was built with;
 *   - `--force`: every platform's optional packages the lock names are installed, not
 *     only this machine's — so the entry's content does not depend on where it was
 *     installed.
 */
import { execFile } from "node:child_process";
import fsp from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { LOCK_FILE } from "./entry.js";

const execFileAsync = promisify(execFile);

/** The root package name an entry's lock is written under. */
export const LOCK_ROOT_NAME = "plugin-store-entry";

/** The flags of the install, after `npm ci`. */
export const INSTALL_FLAGS = [
  "--ignore-scripts",
  "--omit=dev",
  "--force",
  "--no-audit",
  "--no-fund",
] as const;

/** The prefix manifest an entry's package is installed under. */
export function prefixManifest(name: string, version: string): string {
  return `${JSON.stringify(
    { name: LOCK_ROOT_NAME, version: "0.0.0", private: true, dependencies: { [name]: version } },
    null,
    2,
  )}\n`;
}

interface LockPackage {
  version?: unknown;
  dependencies?: unknown;
}

/**
 * The problems of a lock for `name@version`: it must be npm's lockfile v2 or v3, its root
 * must depend on exactly that version, and the package it installs at
 * `node_modules/<name>` must be that version.
 */
export function checkLock(source: string, name: string, version: string): string[] {
  let lock: { lockfileVersion?: unknown; packages?: Record<string, LockPackage> };
  try {
    lock = JSON.parse(source) as typeof lock;
  } catch (err) {
    return [`${LOCK_FILE} is not valid JSON: ${err instanceof Error ? err.message : err}`];
  }
  if (lock === null || typeof lock !== "object") return [`${LOCK_FILE} is not a JSON object`];
  const problems: string[] = [];
  if (lock.lockfileVersion !== 2 && lock.lockfileVersion !== 3) {
    problems.push(`${LOCK_FILE} must be lockfileVersion 2 or 3`);
  }
  const packages = lock.packages;
  if (packages === null || typeof packages !== "object") {
    return [...problems, `${LOCK_FILE} has no "packages"`];
  }
  const rootDeps = packages[""]?.dependencies;
  const declared =
    rootDeps !== null && typeof rootDeps === "object" ? (rootDeps as Record<string, unknown>) : {};
  if (declared[name] !== version || Object.keys(declared).length !== 1) {
    problems.push(`${LOCK_FILE}'s root must depend on exactly ${name} at ${version}`);
  }
  if (packages[`node_modules/${name}`]?.version !== version) {
    problems.push(`${LOCK_FILE} does not install ${name}@${version} at node_modules/${name}`);
  }
  return problems;
}

/** Runs npm in `cwd`; injectable so tests never reach a registry. */
export type NpmRunner = (args: readonly string[], cwd: string) => Promise<void>;

const npmCommand = () => (process.platform === "win32" ? "npm.cmd" : "npm");

export const runNpm: NpmRunner = async (args, cwd) => {
  try {
    await execFileAsync(npmCommand(), [...args], {
      cwd,
      timeout: 300_000,
      maxBuffer: 16 * 1024 * 1024,
      env: process.env,
      shell: process.platform === "win32",
    });
  } catch (err) {
    const stderr = String((err as { stderr?: unknown }).stderr ?? "").trim();
    throw new Error(`npm ${args[0]} failed in ${cwd}${stderr === "" ? "" : `\n${stderr}`}`);
  }
};

/**
 * Installs `name@version` from `lock` into the empty directory `prefix` and answers the
 * package's directory — `<prefix>/node_modules/<name>`, to be read with `packageFiles`
 * against `prefix`.
 */
export async function installFromLock(
  prefix: string,
  name: string,
  version: string,
  lock: string,
  npm: NpmRunner = runNpm,
): Promise<string> {
  await fsp.writeFile(path.join(prefix, "package.json"), prefixManifest(name, version));
  await fsp.writeFile(path.join(prefix, LOCK_FILE), lock);
  await npm(["ci", ...INSTALL_FLAGS], prefix);
  return path.join(prefix, "node_modules", ...name.split("/"));
}

/**
 * Writes the lock for `name@version` into the empty directory `prefix` by resolving it
 * against the registry — nested, so each dependency sits inside the package that needs it
 * — without installing anything. What `pnpm new-entry` records for a new entry.
 */
export async function resolveLock(
  prefix: string,
  name: string,
  version: string,
  npm: NpmRunner = runNpm,
): Promise<string> {
  await fsp.writeFile(path.join(prefix, "package.json"), prefixManifest(name, version));
  await npm(
    [
      "install",
      "--package-lock-only",
      "--install-strategy=nested",
      "--save-exact",
      ...INSTALL_FLAGS,
    ],
    prefix,
  );
  return fsp.readFile(path.join(prefix, LOCK_FILE), "utf8");
}
