/**
 * Builds the artifact this repository exists to publish: `dist/index.json`, the
 * document a PenguinHarness deployment fetches through its HTTP extension registry.
 *
 * The shape is the harness's `ExtensionIndexEntry[]` verbatim — a flat array of entries,
 * one per listed extension, sorted by name. Nothing here is invented for the file format:
 * a consumer that can read the builtin index can read this one, which is the whole
 * point of keeping one index schema across sources.
 *
 * The build is a full rebuild from the manifests, every time (typst's bundler works the
 * same way). There is no incremental state to drift, and `updatedAt` comes from the
 * manifest's last commit rather than from build time, so rebuilding an unchanged
 * repository produces a byte-identical index.
 */
import fs from "node:fs";
import path from "node:path";
import { lastCommitTimestamp, listManifestFiles } from "./lib/git.js";
import { ManifestError, parseManifest } from "./lib/manifest.js";

/** Mirrors `ExtensionIndexEntry` in the harness (packages/server/src/api/types.ts). */
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
  // A yanked extension keeps its manifest — the record of why it was pulled is the point —
  // but leaves the catalogue. Deployments that already installed it are unaffected:
  // the index is discovery, and npm still serves the package.
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
    updatedAt: lastCommitTimestamp(file),
  });
}

if (problems.length > 0) {
  console.error(`Refusing to build an index from ${problems.length} broken manifest(s):\n`);
  for (const problem of problems) console.error(`${problem}\n`);
  process.exit(1);
}

entries.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

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
    <title>PenguinHarness extension index</title>
    <style>
      body { font: 16px/1.6 system-ui, sans-serif; margin: 4rem auto; max-width: 42rem; padding: 0 1rem; }
      code { background: #f4f4f5; border-radius: 4px; padding: 0.1em 0.35em; }
    </style>
  </head>
  <body>
    <h1>PenguinHarness extension index</h1>
    <p>This site serves one artifact: <a href="./index.json"><code>index.json</code></a>, currently listing ${count} extension${count === 1 ? "" : "s"}.</p>
    <p>It is generated from the manifests in
      <a href="https://github.com/Prism-Shadow/penguin-plugins">Prism-Shadow/penguin-plugins</a>.
      To list an extension, open a pull request there.</p>
  </body>
</html>
`;
}
