/**
 * The offline half of the checks: everything decidable from this repository alone.
 *
 * Two groups. Per-file validation (`parseManifest`) says whether a manifest is
 * well-formed. Diff validation says whether the CHANGE is allowed — a version may not
 * go backwards, an extension's name is permanent, and a manifest may only be changed by
 * the people it lists as owners. Those rules exist because the index is a name→package
 * mapping people trust: silently repointing or renaming an entry is the attack this
 * repository has to make impossible.
 *
 * Env: BASE_REF (default origin/main) selects what the change is measured against;
 * PR_AUTHOR, when set, is the GitHub login the ownership rule applies to.
 */
import fs from "node:fs";
import semver from "semver";
import { changedManifests, hasRef, listManifestFiles, mergeBase, readAtRef } from "./lib/git.js";
import { ManifestError, parseManifest, type Manifest } from "./lib/manifest.js";
import { INDEX_MAINTAINERS } from "./lib/vocabulary.js";

const root = process.cwd();
const baseRef = process.env.BASE_REF ?? "origin/main";
const author = process.env.PR_AUTHOR ?? "";
const isMaintainer = author !== "" && INDEX_MAINTAINERS.includes(author);

const problems: string[] = [];

/** Parses every manifest in the working tree; a broken file is reported, not thrown. */
function parseAll(): Map<string, Manifest> {
  const manifests = new Map<string, Manifest>();
  for (const file of listManifestFiles(root)) {
    try {
      manifests.set(file, parseManifest(fs.readFileSync(file, "utf8"), file));
    } catch (err) {
      problems.push(err instanceof ManifestError ? err.message : String(err));
    }
  }
  return manifests;
}

const manifests = parseAll();
console.log(`Checked ${manifests.size} manifest(s) against the schema.`);

if (!hasRef(baseRef)) {
  console.log(`Base ref ${baseRef} is not available; skipping change-level checks.`);
} else {
  const base = mergeBase(baseRef);
  const changes = changedManifests(baseRef);
  console.log(
    changes.length === 0
      ? `No manifest changes against ${baseRef}.`
      : `Checking ${changes.length} manifest change(s) against ${baseRef}.`,
  );

  for (const change of changes) {
    if (change.status === "renamed") {
      problems.push(
        `${change.from} → ${change.file}: an extension's name is permanent. Yank the old entry and submit the new package instead.`,
      );
      continue;
    }
    if (change.status === "deleted") {
      if (!isMaintainer) {
        problems.push(
          `${change.file}: entries are not deleted. Set yanked = true with a yanked_reason to take an extension out of the index.`,
        );
      }
      continue;
    }

    const current = manifests.get(change.file);
    if (current === undefined) continue; // Already reported as malformed.

    const previousSource = readAtRef(base, change.file);
    if (previousSource === null) {
      // New entry: whoever submits it must be one of the owners they are claiming.
      if (author !== "" && !isMaintainer && !current.owners.includes(author)) {
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
      continue; // The base state is already broken; the per-file check covers it.
    }

    if (author !== "" && !isMaintainer && !previous.owners.includes(author)) {
      problems.push(
        `${change.file}: @${author} is not an owner of "${previous.name}". Owners: ${previous.owners
          .map((owner) => `@${owner}`)
          .join(", ")}.`,
      );
    }
    if (semver.lt(current.version, previous.version)) {
      problems.push(
        `${change.file}: version must not decrease (${previous.version} → ${current.version}).`,
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
