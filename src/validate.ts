/**
 * The offline half of the checks: everything decidable from this repository alone.
 *
 * Two groups. Per-entry validation says whether an entry is well-formed: its manifest
 * (`parseManifest`, which also checks that the path spells its name, version and key),
 * and its lock (`checkLock`). Diff validation says whether the CHANGE is allowed — an
 * entry's path is permanent, an entry is yanked rather than deleted, and only the people
 * a name lists as owners may add or edit its entries. Those rules exist because the
 * index is a name→content mapping people trust: silently repointing an entry is the
 * attack this repository has to make impossible.
 *
 * A new version, or a new content of one version, is a new entry beside the others;
 * nothing here orders versions — the consumer picks by semver.
 *
 * Env: BASE_REF (default origin/main) selects what the change is measured against;
 * PR_AUTHOR, when set, is the GitHub login the ownership rule applies to.
 */
import fs from "node:fs";
import path from "node:path";
import {
  checkLock,
  LOCK_FILE,
  ManifestError,
  PACKAGES_DIR,
  parseEntryPath,
  parseManifest,
  type Manifest,
} from "@prismshadow/penguin-plugin-index";
import {
  changedEntries,
  hasRef,
  listManifestFiles,
  manifestsAtRef,
  mergeBase,
  readAtRef,
  strayFiles,
} from "./lib/git.js";
import { INDEX_MAINTAINERS } from "./lib/vocabulary.js";

const root = process.cwd();
const baseRef = process.env.BASE_REF ?? "origin/main";
const author = process.env.PR_AUTHOR ?? "";
const isMaintainer = author !== "" && INDEX_MAINTAINERS.includes(author);

const problems: string[] = [];

/** Parses every entry in the working tree; a broken one is reported, not thrown. */
function parseAll(): Map<string, Manifest> {
  const manifests = new Map<string, Manifest>();
  for (const file of listManifestFiles(root)) {
    let manifest: Manifest;
    try {
      manifest = parseManifest(fs.readFileSync(file, "utf8"), file);
    } catch (err) {
      problems.push(err instanceof ManifestError ? err.message : String(err));
      continue;
    }
    const hasNpmChannel = manifest.channels.some((channel) => channel.kind === "npm");
    if (!hasNpmChannel) {
      manifests.set(file, manifest);
      continue;
    }
    const lockFile = path.posix.join(path.posix.dirname(file), LOCK_FILE);
    if (!fs.existsSync(lockFile)) {
      problems.push(`${file}:\n  - the npm channel requires a ${LOCK_FILE} beside the manifest`);
      continue;
    }
    const lockProblems = checkLock(
      fs.readFileSync(lockFile, "utf8"),
      manifest.name,
      manifest.version,
    );
    if (lockProblems.length > 0) {
      problems.push([`${lockFile}:`, ...lockProblems.map((p) => `  - ${p}`)].join("\n"));
      continue;
    }
    manifests.set(file, manifest);
  }
  return manifests;
}

const manifests = parseAll();
console.log(
  `Checked ${manifests.size} entr${manifests.size === 1 ? "y" : "ies"} against the schema.`,
);
for (const file of strayFiles(root)) {
  problems.push(
    `${file}: not part of an entry. packages/ holds [<@scope>/]<bucket>/<name>/<version>/ directories with a manifest.toml — <version>/<key>/ once several contents share the version — and a package-lock.json when the entry has an npm channel — nothing else.`,
  );
}

/**
 * A keyless path claims its name and version have exactly one content, so it may not
 * sit beside any other entry of the same name and version — the claim is what lets the
 * store treat `…/<version>/` as the entry.
 */
{
  const byNameVersion = new Map<string, { file: string; keyless: boolean }[]>();
  for (const [file, manifest] of manifests) {
    const entry = parseEntryPath(file.slice(PACKAGES_DIR.length + 1));
    if (entry === null) continue;
    const id = `${manifest.name}@${manifest.version}`;
    const list = byNameVersion.get(id);
    const item = { file, keyless: entry.key === undefined };
    if (list === undefined) byNameVersion.set(id, [item]);
    else list.push(item);
  }
  for (const [id, items] of byNameVersion) {
    if (items.length < 2) continue;
    for (const { file, keyless } of items) {
      if (!keyless) continue;
      problems.push(
        `${file}: ${items.length} entries share "${id}" — a keyless entry (no <key> directory) claims to be the only content of its name and version; file it under a <key> directory or drop it.`,
      );
    }
  }
}

/** The owners of a name at the base: everyone listed by any of its entries there. */
function ownersAtBase(base: string, name: string): Set<string> {
  const owners = new Set<string>();
  for (const file of manifestsAtRef(base, name)) {
    const source = readAtRef(base, file);
    if (source === null) continue;
    try {
      for (const owner of parseManifest(source, file).owners) owners.add(owner);
    } catch {
      // A broken base entry grants nothing.
    }
  }
  return owners;
}

if (!hasRef(baseRef)) {
  console.log(`Base ref ${baseRef} is not available; skipping change-level checks.`);
} else {
  const base = mergeBase(baseRef);
  const changes = changedEntries(baseRef);
  console.log(
    changes.length === 0
      ? `No entry changes against ${baseRef}.`
      : `Checking ${changes.length} entry change(s) against ${baseRef}.`,
  );

  for (const change of changes) {
    if (change.status === "renamed") {
      // An entry's path spells its content, so a schema change moves every entry at
      // once. A maintainer may carry that migration; a contributor may not.
      if (!isMaintainer) {
        problems.push(
          `${change.from} → ${change.file}: an entry's path is permanent — it spells the name, the version and the content. Add the new entry beside it and yank the old one instead.`,
        );
      }
      continue;
    }
    if (change.status === "deleted") {
      if (!isMaintainer) {
        problems.push(
          `${change.file}: entries are not deleted. Set yanked = true with a yanked_reason to take an entry out of the index.`,
        );
      }
      continue;
    }

    const current = manifests.get(change.file);
    if (current === undefined) continue; // Already reported as malformed.
    if (author === "" || isMaintainer) continue;

    const previousSource = readAtRef(base, change.file);
    if (previousSource === null) {
      // A new entry: under a listed name, only that name's owners add to it; under a new
      // name, whoever submits it must be one of the owners they are claiming.
      const owners = ownersAtBase(base, current.name);
      if (owners.size > 0 && !owners.has(author)) {
        problems.push(
          `${change.file}: @${author} is not an owner of "${current.name}". Owners: ${[...owners]
            .map((owner) => `@${owner}`)
            .join(", ")}.`,
        );
      } else if (owners.size === 0 && !current.owners.includes(author)) {
        problems.push(
          `${change.file}: @${author} submitted this entry but is not listed in "owners".`,
        );
      }
      continue;
    }

    let previous: Manifest;
    try {
      previous = parseManifest(previousSource, change.file);
    } catch {
      continue; // The base state is already broken; the per-entry check covers it.
    }
    if (!previous.owners.includes(author)) {
      problems.push(
        `${change.file}: @${author} is not an owner of "${previous.name}". Owners: ${previous.owners
          .map((owner) => `@${owner}`)
          .join(", ")}.`,
      );
    }
  }
}

if (problems.length > 0) {
  console.error(`\n${problems.length} problem(s) found:\n`);
  for (const problem of problems) console.error(`${problem}\n`);
  process.exit(1);
}
console.log("All checks passed.");
