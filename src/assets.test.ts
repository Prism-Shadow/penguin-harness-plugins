/**
 * The asset cache the published tree carries: each listed entry's `icon.svg` and
 * `README.md`, read out of the one tarball the entry's manifest pins and served from this
 * site.
 *
 * - A tarball whose bytes are not what the manifest's `integrity` names is refused, and
 *   the refusal names the entry: the cache must hold the package's own files, not
 *   whatever answered the URL.
 * - The files come from the package root (`package/icon.svg`, `package/README.md`); a
 *   package that ships neither yields neither.
 * - The cache is content-addressed by name and version: a second call for an entry whose
 *   files are already cached fetches nothing.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { filesUnder, packArchive, type Manifest } from "@prismshadow/penguin-plugin-index";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PAGES_BASE_URL, collectAssets } from "./assets.js";

const MANIFEST: Manifest = {
  name: "@acme/widgets",
  version: "1.2.3",
  description: "Widgets.",
  authors: ["Ada <@ada>"],
  license: "MIT",
  repository: "https://github.com/acme/widgets",
  categories: ["software-development"],
  owners: ["ada"],
  integrity: "",
  channels: [],
};

/** A tarball shaped like `npm pack`'s: everything under `package/`, gzipped. */
async function tarballOf(files: Record<string, string>): Promise<Buffer> {
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-assets-test-"));
  for (const [rel, content] of Object.entries(files)) {
    const abs = path.join(staging, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, content);
  }
  const bytes = await packArchive(await filesUnder(staging, "package"));
  fs.rmSync(staging, { recursive: true, force: true });
  return bytes;
}

/** A fetch that answers every call with one body, and counts them. */
function serving(body: Buffer): { fetch: typeof fetch; calls: { url: string }[] } {
  const calls: { url: string }[] = [];
  const impl = async (input: string | URL | Request): Promise<Response> => {
    calls.push({ url: input instanceof Request ? input.url : String(input) });
    return new Response(body);
  };
  return { fetch: impl as typeof fetch, calls };
}

const sha512 = (bytes: Uint8Array) =>
  `sha512-${createHash("sha512").update(bytes).digest("base64")}`;

describe("collectAssets", () => {
  let cache: string;
  beforeEach(() => {
    cache = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-assets-cache-"));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    fs.rmSync(cache, { recursive: true, force: true });
  });

  it("extracts the package's icon and readme and answers the URLs this site serves them at", async () => {
    const bytes = await tarballOf({
      "icon.svg": "<svg></svg>",
      "README.md": "# Widgets",
      "package.json": '{"name":"@acme/widgets"}',
    });
    const manifest = { ...MANIFEST, integrity: sha512(bytes) };
    const { fetch, calls } = serving(bytes);
    const assets = await collectAssets(manifest, cache, { fetchImpl: fetch });
    expect(assets).toEqual({
      icon: `${PAGES_BASE_URL}/assets/@acme/widgets/1.2.3/icon.svg`,
      readme: `${PAGES_BASE_URL}/assets/@acme/widgets/1.2.3/README.md`,
    });
    expect(calls).toHaveLength(1);
    expect(fs.readFileSync(path.join(cache, "@acme/widgets/1.2.3/icon.svg"), "utf8")).toBe(
      "<svg></svg>",
    );
    expect(fs.readFileSync(path.join(cache, "@acme/widgets/1.2.3/README.md"), "utf8")).toBe(
      "# Widgets",
    );
  });

  it("answers neither URL when the package ships neither file", async () => {
    const bytes = await tarballOf({ "package.json": '{"name":"@acme/widgets"}' });
    const manifest = { ...MANIFEST, integrity: sha512(bytes) };
    const { fetch } = serving(bytes);
    expect(await collectAssets(manifest, cache, { fetchImpl: fetch })).toEqual({});
  });

  it("refuses a tarball the manifest does not pin, naming the entry", async () => {
    const bytes = await tarballOf({ "README.md": "tampered" });
    const manifest = { ...MANIFEST, integrity: sha512(new Uint8Array([1, 2, 3])) };
    const { fetch } = serving(bytes);
    await expect(collectAssets(manifest, cache, { fetchImpl: fetch })).rejects.toThrow(
      /@acme\/widgets@1\.2\.3/,
    );
    expect(fs.existsSync(path.join(cache, "@acme"))).toBe(false);
  });

  it("refuses a source that answers an error status", async () => {
    const dead = (async () => new Response("gone", { status: 404 })) as typeof fetch;
    await expect(
      collectAssets({ ...MANIFEST, integrity: sha512(new Uint8Array([1])) }, cache, {
        fetchImpl: dead,
      }),
    ).rejects.toThrow(/HTTP 404/);
  });

  it("copies from the cache instead of fetching again for an entry it already has", async () => {
    const bytes = await tarballOf({ "README.md": "# Widgets" });
    const manifest = { ...MANIFEST, integrity: sha512(bytes) };
    const { fetch, calls } = serving(bytes);
    await collectAssets(manifest, cache, { fetchImpl: fetch });
    expect(calls).toHaveLength(1);
    const again = await collectAssets(manifest, cache, { fetchImpl: fetch });
    expect(again).toEqual({
      readme: `${PAGES_BASE_URL}/assets/@acme/widgets/1.2.3/README.md`,
    });
    expect(calls).toHaveLength(1);
  });
});
