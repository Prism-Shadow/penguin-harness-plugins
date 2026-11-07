/**
 * Writes a new entry for a published package: `pnpm new-entry <name>@<version>`.
 *
 * Nobody computes an integrity by hand. This resolves the package's lock against the
 * registry, installs from it exactly as `pnpm check-npm` will (the shared package's one
 * install), archives and hashes what it produced, and writes the entry's keyless
 * directory `packages/[<@scope>/]<bucket>/<name>/<version>/` with the lock and a
 * manifest filled in from the package's own package.json. `categories` and `owners` are
 * left empty for the submitter to fill: `pnpm validate` says so until they are.
 *
 * Like the checks, it runs none of the package's code.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  archiveIntegrity,
  installFromLock,
  isPackageName,
  keylessEntryDir,
  LOCK_FILE,
  MANIFEST_FILE,
  packageFiles,
  PACKAGES_DIR,
  resolveLock,
} from "@prismshadow/penguin-plugin-index";
import semver from "semver";
import { stringify as stringifyToml } from "smol-toml";
import { fetchPackument, repositoryUrl } from "./lib/npm.js";

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
  console.log("Reading the registry's integrity…");
  const packument = await fetchPackument(name);
  const published = packument.versions?.[version];
  const integrity = published?.dist?.integrity;
  if (typeof integrity !== "string") {
    console.error(`the registry reports no integrity for ${name}@${version}`);
    process.exit(1);
  }
  const { size } = await archiveIntegrity(await packageFiles(pkgDir, installDir));
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
  const penguin =
    typeof pkg.penguin === "object" && pkg.penguin !== null
      ? (pkg.penguin as Record<string, unknown>)
      : {};
  const title =
    typeof penguin.title === "string" && penguin.title.trim() !== "" ? penguin.title : undefined;
  const titleZh =
    typeof penguin.title_zh === "string" && penguin.title_zh.trim() !== ""
      ? penguin.title_zh
      : undefined;
  const descriptionZh =
    typeof penguin.description_zh === "string" && penguin.description_zh.trim() !== ""
      ? penguin.description_zh
      : undefined;
  const manifest = {
    name,
    version,
    description: typeof pkg.description === "string" ? pkg.description : "",
    authors,
    license: typeof pkg.license === "string" ? pkg.license : "",
    ...(repository !== undefined ? { repository } : {}),
    ...(typeof pkg.homepage === "string" ? { homepage: pkg.homepage } : {}),
    ...(title !== undefined ? { title } : {}),
    ...(titleZh !== undefined ? { title_zh: titleZh } : {}),
    ...(descriptionZh !== undefined ? { description_zh: descriptionZh } : {}),
    ...(keywords.length > 0 ? { keywords } : {}),
    categories: [],
    owners: [],
    integrity,
    channels: [{ kind: "npm" }],
  };

  const dir = path.join(PACKAGES_DIR, ...keylessEntryDir(name, version).split("/"));
  if (fs.existsSync(path.join(dir, MANIFEST_FILE))) {
    console.error(`${dir}/${MANIFEST_FILE} already exists — ${name}@${version} is already entered`);
    process.exit(1);
  }
  if (fs.existsSync(dir)) {
    console.error(
      `${dir} already holds keyed entries — several contents share ${name}@${version}, and a keyless entry may not sit beside them (pnpm validate would refuse it)`,
    );
    process.exit(1);
  }
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, MANIFEST_FILE), stringifyToml(manifest) + "\n");
  fs.writeFileSync(path.join(dir, LOCK_FILE), lock);
  console.log(`Wrote ${dir}/ (${integrity}, ${size} bytes archived).`);
  console.log(`Fill in "categories" and "owners" in ${MANIFEST_FILE}, then run pnpm validate.`);
} finally {
  fs.rmSync(scratch, { recursive: true, force: true });
}
