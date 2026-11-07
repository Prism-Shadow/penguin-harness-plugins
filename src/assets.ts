/**
 * The card and readme assets the Pages site serves for each listed entry.
 *
 * An entry names no icon and no readme of its own: what the card draws and the detail view
 * shows is whatever the package itself carries — its `icon.svg` and its `README.md` at the
 * package root. The build downloads the one tarball the entry's manifest pins (its channels
 * all serve those same bytes; the `integrity` is what they are checked against before
 * anything is kept), reads those two files, and keeps them in the published tree, so a
 * deployment's server fetches them from this site — a cache of the package's own bytes —
 * rather than from wherever the package happens to live. An entry whose package ships
 * neither file simply carries no URL.
 *
 * Downloads are cached content-addressed under `.assets/` (gitignored): a rebuild of an
 * unchanged tree re-serves the files it already has and fetches nothing.
 */
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { Manifest } from "@prismshadow/penguin-plugin-index";

const execFileAsync = promisify(execFile);

const FETCH_TIMEOUT_MS = 60_000;
/** A tarball worth opening: at most the size CI already accepts for a whole entry. */
const MAX_TARBALL_BYTES = 64 * 1024 * 1024;

/** The user agent the index repository's own fetches go out as (see lib/npm.ts's registry reads). */
const USER_AGENT = "penguin-harness-plugins (index build)";

/** The site's own base URL: where the published tree is served. */
export const PAGES_BASE_URL =
  (process.env.PAGES_BASE_URL ?? "https://prism-shadow.github.io/penguin-harness-plugins").replace(
    /\/+$/,
    "",
  );

/** The two files an entry's card and detail view are drawn from, at the package root. */
const WANTED = ["icon.svg", "README.md"] as const;

export type WantedFile = (typeof WANTED)[number];

export interface EntryAssets {
  /** URL of the served `icon.svg`, when the package carries one. */
  icon?: string;
  /** URL of the served `README.md`, when the package carries one. */
  readme?: string;
}

/** sha512 as npm's integrity spells it, of the bytes the channel serves. */
function integrityOf(bytes: Uint8Array): string {
  return `sha512-${createHash("sha512").update(bytes).digest("base64")}`;
}

/** The tarball URL: the channel's own when one is listed, else the registry's standard shape. */
function tarballUrl(manifest: Manifest): string {
  const channel = manifest.channels.find((c) => c.url !== undefined);
  if (channel !== undefined) return channel.url as string;
  const file = manifest.name.replace(/^@\//, "").replace("/", "-");
  return `https://registry.npmjs.org/${manifest.name}/-/${file}-${manifest.version}.tgz`;
}

async function download(url: string, fetchImpl: typeof fetch): Promise<Uint8Array> {
  const res = await fetchImpl(url, {
    headers: { "user-agent": USER_AGENT },
    redirect: "follow",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`${url} answered HTTP ${res.status}`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (bytes.byteLength > MAX_TARBALL_BYTES) throw new Error(`${url} is over the size limit`);
  return bytes;
}

/** Opens the tarball and copies the wanted package-root files into `dest`. */
async function extract(bytes: Uint8Array, dest: string): Promise<void> {
  const staging = await fsp.mkdtemp(path.join(os.tmpdir(), "penguin-index-assets-"));
  try {
    // Relative file names and a cwd: a Windows path carries a drive letter, which GNU tar
    // reads as a remote host name.
    await fsp.writeFile(path.join(staging, "asset.tgz"), bytes);
    await execFileAsync("tar", ["-xzf", "asset.tgz", "package"], { cwd: staging });
    await fsp.mkdir(dest, { recursive: true });
    for (const file of WANTED) {
      const from = path.join(staging, "package", file);
      if (fs.existsSync(from)) await fsp.copyFile(from, path.join(dest, file));
    }
  } finally {
    await fsp.rm(staging, { recursive: true, force: true });
  }
}

export interface AssetOptions {
  fetchImpl?: typeof fetch;
}

/**
 * Fills the published tree's asset cache for one entry and answers the URLs it will be
 * served at. The cache directory is content-addressed by the entry's `integrity`, so a
 * rebuild of an unchanged entry copies what it already has and a changed package is a new
 * directory.
 */
export async function collectAssets(
  manifest: Manifest,
  cacheRoot: string,
  { fetchImpl = fetch }: AssetOptions = {},
): Promise<EntryAssets> {
  const dir = path.join(cacheRoot, manifest.name, manifest.version);
  const have = async (file: WantedFile) => fs.existsSync(path.join(dir, file));
  if (!(await have("icon.svg")) && !(await have("README.md"))) {
    const bytes = await download(tarballUrl(manifest), fetchImpl);
    if (bytes.byteLength === 0 || integrityOf(bytes) !== manifest.integrity) {
      throw new Error(`${manifest.name}@${manifest.version}: the served tarball is not the one the manifest pins`);
    }
    await extract(bytes, dir);
  }
  const base = `${PAGES_BASE_URL}/assets/${manifest.name}/${manifest.version}`;
  return {
    ...(await have("icon.svg") ? { icon: `${base}/icon.svg` } : {}),
    ...(await have("README.md") ? { readme: `${base}/README.md` } : {}),
  };
}
