/**
 * Builds the artifact this repository exists to publish: `dist/index.json`, the
 * document a PenguinHarness deployment fetches through its HTTP plugin registry.
 *
 * A flat array of entries, one per entry directory under `packages/` that is not yanked,
 * each carrying its `integrity`. A name may appear more than once — several versions,
 * or several contents of one version — and the consumer picks by semver; the
 * integrity is what it checks a fetched package against before storing it. The shape
 * is the one a machine's plugin store writes to its own `index.json`, so a client that
 * reads one reads the other.
 *
 * The build is a full rebuild from the tree, every time (typst's bundler works the same
 * way). There is no incremental state to drift, and `updatedAt` comes from the
 * manifest's last commit rather than from build time, so rebuilding an unchanged
 * repository produces a byte-identical index.
 */
import fs from "node:fs";
import path from "node:path";
import { ManifestError, parseManifest } from "@prismshadow/penguin-plugin-index";
import semver from "semver";
import { lastCommitTimestamp, listManifestFiles } from "./lib/git.js";

interface IndexEntry {
  name: string;
  version: string;
  description: string;
  authors: string[];
  license: string;
  repository?: string;
  homepage?: string;
  keywords?: string[];
  categories?: string[];
  integrity: string;
  updatedAt?: number;
}

const root = process.cwd();
const outDir = path.join(root, process.env.OUT_DIR ?? "dist");

const entries: IndexEntry[] = [];
const problems: string[] = [];
let yanked = 0;

for (const file of listManifestFiles(root)) {
  let manifest;
  try {
    manifest = parseManifest(fs.readFileSync(file, "utf8"), file);
  } catch (err) {
    problems.push(err instanceof ManifestError ? err.message : String(err));
    continue;
  }
  // A yanked entry keeps its manifest — the record of why it was pulled is the point —
  // but leaves the catalogue. Deployments that already stored it are unaffected: the
  // index is discovery, and npm still serves the package.
  if (manifest.yanked === true) {
    yanked += 1;
    continue;
  }
  entries.push({
    name: manifest.name,
    version: manifest.version,
    description: manifest.description,
    authors: manifest.authors,
    license: manifest.license,
    repository: manifest.repository,
    ...(manifest.homepage !== undefined ? { homepage: manifest.homepage } : {}),
    ...(manifest.keywords !== undefined ? { keywords: manifest.keywords } : {}),
    categories: manifest.categories,
    integrity: manifest.integrity,
    updatedAt: lastCommitTimestamp(file),
  });
}

if (problems.length > 0) {
  console.error(`Refusing to build an index from ${problems.length} broken manifest(s):\n`);
  for (const problem of problems) console.error(`${problem}\n`);
  process.exit(1);
}

const byCodeUnit = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);
entries.sort(
  (a, b) =>
    byCodeUnit(a.name, b.name) ||
    semver.compare(a.version, b.version) ||
    byCodeUnit(a.integrity, b.integrity),
);

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, "index.json"), JSON.stringify(entries));
fs.writeFileSync(path.join(outDir, "index.html"), landingPage(entries.length));
console.log(`Wrote ${entries.length} entries to ${path.join(outDir, "index.json")}.`);
if (yanked > 0) console.log(`Omitted ${yanked} yanked entr${yanked === 1 ? "y" : "ies"}.`);

/** A one-page site so the Pages deployment has a root that explains what index.json is. */
function landingPage(count: number): string {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>PenguinHarness plugin index</title>
    <style>
      body { font: 16px/1.6 system-ui, sans-serif; margin: 4rem auto; max-width: 42rem; padding: 0 1rem; }
      code { background: #f4f4f5; border-radius: 4px; padding: 0.1em 0.35em; }
    </style>
  </head>
  <body>
    <h1>PenguinHarness plugin index</h1>
    <p>This site serves one artifact: <a href="./index.json"><code>index.json</code></a>, currently listing ${count} entr${count === 1 ? "y" : "ies"}.</p>
    <p>It is generated from the entries in
      <a href="https://github.com/Prism-Shadow/penguin-plugins">Prism-Shadow/penguin-plugins</a>.
      To list a plugin, open a pull request there.</p>
  </body>
</html>
`;
}
