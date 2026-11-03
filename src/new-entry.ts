/**
 * Writes a new entry for a published package: `pnpm new-entry <name>@<version>`.
 *
 * Nobody computes an integrity by hand. This resolves the package's lock against the
 * registry, installs from it exactly as `pnpm check-npm` will (the shared package's one
 * install), archives and hashes what it produced, and writes
 * `plugins/<name>/<version>/<key>/` with the lock and a manifest filled in from the
 * package's own package.json. `categories` and `owners` are left empty for the submitter
 * to fill: `pnpm validate` says so until they are.
 *
 * Like the checks, it runs none of the package's code.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  archiveIntegrity,
  entryDir,
  installFromLock,
  isPackageName,
  LOCK_FILE,
  MANIFEST_FILE,
  packageFiles,
  PLUGINS_DIR,
  resolveLock,
} from "@prismshadow/penguin-plugin-index";
import semver from "semver";
import { stringify as stringifyToml } from "smol-toml";
import { repositoryUrl } from "./lib/npm.js";

const spec = process.argv[2] ?? "";
const at = spec.lastIndexOf("@");
const name = at > 0 ? spec.slice(0, at) : "";
const version = at > 0 ? spec.slice(at + 1) : "";
if (!isPackageName(name) || semver.valid(version) !== version) {
  console.error("Usage: pnpm new-entry <npm name>@<exact version>");
  process.exit(2);
}

/** An author as this index writes one: a display name, optionally `<contact>`. */
function authorOf(value: unknown): string | null {
  if (typeof value === "string") return value.trim() === "" ? null : value.trim();
  if (value === null || typeof value !== "object") return null;
  const a = value as { name?: unknown; email?: unknown; url?: unknown };
  if (typeof a.name !== "string" || a.name.trim() === "") return null;
  const contact = typeof a.email === "string" ? a.email : typeof a.url === "string" ? a.url : null;
  return contact === null ? a.name.trim() : `${a.name.trim()} <${contact}>`;
}

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-new-entry-"));
try {
  const resolveDir = path.join(scratch, "resolve");
  const installDir = path.join(scratch, "install");
  fs.mkdirSync(resolveDir);
  fs.mkdirSync(installDir);
  console.log(`Resolving ${name}@${version}…`);
  const lock = await resolveLock(resolveDir, name, version);
  console.log("Installing from the lock…");
  const pkgDir = await installFromLock(installDir, name, version, lock);
  const { integrity, size } = await archiveIntegrity(await packageFiles(pkgDir, installDir));
  const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, "package.json"), "utf8")) as Record<
    string,
    unknown
  >;

  const authors = [pkg.author, ...(Array.isArray(pkg.contributors) ? pkg.contributors : [])]
    .map(authorOf)
    .filter((a): a is string => a !== null);
  const repository = repositoryUrl(pkg.repository) ?? undefined;
  const keywords = Array.isArray(pkg.keywords)
    ? pkg.keywords.filter((k): k is string => typeof k === "string")
    : [];
  const manifest = {
    name,
    version,
    description: typeof pkg.description === "string" ? pkg.description : "",
    authors,
    license: typeof pkg.license === "string" ? pkg.license : "",
    ...(repository !== undefined ? { repository } : {}),
    ...(typeof pkg.homepage === "string" ? { homepage: pkg.homepage } : {}),
    ...(keywords.length > 0 ? { keywords } : {}),
    categories: [],
    owners: [],
    integrity,
  };

  const dir = path.join(PLUGINS_DIR, ...entryDir(name, version, integrity).split("/"));
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, MANIFEST_FILE), stringifyToml(manifest) + "\n");
  fs.writeFileSync(path.join(dir, LOCK_FILE), lock);
  console.log(`Wrote ${dir}/ (${integrity}, ${size} bytes archived).`);
  console.log(`Fill in "categories" and "owners" in ${MANIFEST_FILE}, then run pnpm validate.`);
} finally {
  fs.rmSync(scratch, { recursive: true, force: true });
}
