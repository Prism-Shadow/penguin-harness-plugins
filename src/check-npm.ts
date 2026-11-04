/**
 * The online half of the checks: does npm actually serve what the entry claims?
 *
 * Scoped to the entries a pull request touches (zed's `changedExtensionIds` rule) —
 * re-installing every listed package on every run would make CI slow and would fail the
 * repository whenever an unrelated author's registry entry hiccups. `--all` forces a
 * full sweep for the periodic audit.
 */
import fs from "node:fs";
import path from "node:path";
import { LOCK_FILE, ManifestError, parseManifest } from "@prismshadow/penguin-plugin-index";
import { changedEntries, hasRef, listManifestFiles } from "./lib/git.js";
import { checkAgainstNpm, checkChannels } from "./lib/npm.js";

const root = process.cwd();
const baseRef = process.env.BASE_REF ?? "origin/main";
const all = process.argv.includes("--all");

function targets(): string[] {
  if (all || !hasRef(baseRef)) return listManifestFiles(root);
  return changedEntries(baseRef)
    .filter((change) => change.status === "added" || change.status === "modified")
    .map((change) => change.file);
}

const files = targets();
if (files.length === 0) {
  console.log("No entries to check against npm.");
  process.exit(0);
}

const problems: string[] = [];
for (const file of files) {
  let manifest;
  try {
    manifest = parseManifest(fs.readFileSync(file, "utf8"), file);
  } catch (err) {
    problems.push(err instanceof ManifestError ? err.message : `${file}:\n  - ${String(err)}`);
    continue;
  }
  if (manifest.yanked === true) {
    console.log(`${manifest.name}@${manifest.version}: yanked, skipping channel checks.`);
    continue;
  }
  const npmChannel = manifest.channels.find((channel) => channel.kind === "npm");
  if (npmChannel !== undefined) {
    console.log(
      `Checking ${manifest.name}@${manifest.version} (${manifest.integrity}) against npm…`,
    );
    const lockPath = path.posix.join(path.posix.dirname(file), LOCK_FILE);
    let lock: string;
    try {
      lock = fs.readFileSync(lockPath, "utf8");
    } catch {
      problems.push(`${file}:\n  - the npm channel requires a ${LOCK_FILE} beside the manifest`);
      lock = "";
    }
    if (lock !== "") {
      const found = await checkAgainstNpm(manifest, lock);
      for (const problem of found) {
        problems.push([`${file}:`, `  - ${problem}`].join("\n"));
      }
    }
  }
  const urlChannels = manifest.channels.filter((channel) => channel.url !== undefined);
  if (urlChannels.length > 0) {
    console.log(`Checking ${urlChannels.length} distribution channel(s)…`);
    for (const problem of await checkChannels(manifest)) {
      problems.push([`${file}:`, `  - ${problem}`].join("\n"));
    }
  }
}

if (problems.length > 0) {
  console.error(
    `\n${problems.length} entr${problems.length === 1 ? "y" : "ies"} did not match npm:\n`,
  );
  for (const problem of problems) console.error(`${problem}\n`);
  process.exit(1);
}
console.log("All checked entries install to the content their integrity names.");
