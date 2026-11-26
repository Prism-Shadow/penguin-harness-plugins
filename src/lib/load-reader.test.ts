/**
 * Loading the readers that harness releases publish.
 *
 * - Every version published from the floor up is loaded, oldest first; an older one is not.
 * - Each is the release's own code, imported through its package's `exports` entry for the
 *   reader, unpacked from the tarball the registry serves.
 * - A tarball whose bytes are not the registry's integrity is never run: that version fails to
 *   load, named, and the others still load.
 * - A version whose package has no reader export fails to load, named.
 * - A registry that cannot be read is a failure, not an empty list of readers.
 * - A reader in source loads from its file.
 */
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { filesUnder, packArchive } from "@prismshadow/penguin-plugin-index";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { publishedReaders, sourceReader } from "./load-reader.js";

const PKG = "@acme/core";
const EXPORT = "./plugin-index";

const sha512 = (bytes: Uint8Array) =>
  `sha512-${createHash("sha512").update(bytes).digest("base64")}`;

const tarballUrl = (version: string) =>
  `https://registry.npmjs.org/@acme/core/-/core-${version}.tgz`;

/** A published reader package as `npm pack` lays one out; its reader answers its own version. */
async function readerTarball(version: string, exports: unknown): Promise<Buffer> {
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-reader-pkg-"));
  fs.mkdirSync(path.join(staging, "dist/plugin"), { recursive: true });
  fs.writeFileSync(
    path.join(staging, "package.json"),
    JSON.stringify({ name: PKG, version, type: "module", exports }),
  );
  fs.writeFileSync(
    path.join(staging, "dist/plugin/index-document.js"),
    `export function parsePluginIndex(data, source, options) {\n` +
      `  return { reader: ${JSON.stringify(version)}, data, source, options };\n}\n`,
  );
  const bytes = await packArchive(await filesUnder(staging, "package"));
  fs.rmSync(staging, { recursive: true, force: true });
  return bytes;
}

const READER_EXPORTS = {
  [EXPORT]: {
    types: "./dist/plugin/index-document.d.ts",
    import: "./dist/plugin/index-document.js",
  },
};

interface Published {
  bytes: Buffer;
  /** The integrity the registry reports, the bytes' own unless a test says otherwise. */
  integrity?: string;
}

/** The registry: the package's document and each version's tarball. */
function registry(published: Record<string, Published>): typeof fetch {
  const packument = {
    name: PKG,
    versions: Object.fromEntries(
      Object.entries(published).map(([version, { bytes, integrity }]) => [
        version,
        { version, dist: { tarball: tarballUrl(version), integrity: integrity ?? sha512(bytes) } },
      ]),
    ),
  };
  return (async (input: string | URL | Request) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url === `https://registry.npmjs.org/${encodeURIComponent(PKG)}`) {
      return new Response(JSON.stringify(packument));
    }
    const version = Object.keys(published).find((v) => tarballUrl(v) === url);
    return version === undefined
      ? new Response("not found", { status: 404 })
      : new Response(published[version]!.bytes);
  }) as typeof fetch;
}

describe("publishedReaders", () => {
  let workDir: string;
  beforeEach(() => {
    workDir = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-readers-"));
  });
  afterEach(() => fs.rmSync(workDir, { recursive: true, force: true }));

  it("loads every version from the floor up, oldest first, each through its reader export", async () => {
    const fetchImpl = registry({
      "0.2.20": { bytes: await readerTarball("0.2.20", READER_EXPORTS) },
      "0.2.18": { bytes: await readerTarball("0.2.18", READER_EXPORTS) },
      "0.2.19": { bytes: await readerTarball("0.2.19", READER_EXPORTS) },
      "0.2.19-rc.1": { bytes: await readerTarball("0.2.19-rc.1", READER_EXPORTS) },
    });
    const { readers, failures } = await publishedReaders(PKG, EXPORT, "0.2.19", workDir, {
      fetchImpl,
    });
    expect(failures).toEqual([]);
    expect(readers.map((reader) => reader.label)).toEqual([
      "@acme/core@0.2.19",
      "@acme/core@0.2.20",
    ]);
    expect(readers.map((reader) => reader.parse([], "index.json", { icons: "drop" }))).toEqual([
      { reader: "0.2.19", data: [], source: "index.json", options: { icons: "drop" } },
      { reader: "0.2.20", data: [], source: "index.json", options: { icons: "drop" } },
    ]);
  });

  it("never runs a tarball that is not the registry's, and still loads the others", async () => {
    const fetchImpl = registry({
      "0.2.19": {
        bytes: await readerTarball("0.2.19", READER_EXPORTS),
        integrity: sha512(new Uint8Array([1, 2, 3])),
      },
      "0.2.20": { bytes: await readerTarball("0.2.20", READER_EXPORTS) },
    });
    const { readers, failures } = await publishedReaders(PKG, EXPORT, "0.2.19", workDir, {
      fetchImpl,
    });
    expect(readers.map((reader) => reader.label)).toEqual(["@acme/core@0.2.20"]);
    expect(failures).toEqual([
      { label: "@acme/core@0.2.19", error: expect.stringContaining("the registry pins") },
    ]);
    expect(fs.existsSync(path.join(workDir, "0.2.19"))).toBe(false);
  });

  it("names a version whose package has no reader export", async () => {
    const fetchImpl = registry({
      "0.2.19": { bytes: await readerTarball("0.2.19", { ".": "./dist/index.js" }) },
    });
    expect(await publishedReaders(PKG, EXPORT, "0.2.19", workDir, { fetchImpl })).toEqual({
      readers: [],
      failures: [
        { label: "@acme/core@0.2.19", error: '@acme/core@0.2.19 has no "./plugin-index" export' },
      ],
    });
  });

  it("fails, rather than finding no reader, when the registry cannot be read", async () => {
    const down = (async () => new Response("unavailable", { status: 503 })) as typeof fetch;
    expect(await publishedReaders(PKG, EXPORT, "0.2.19", workDir, { fetchImpl: down })).toEqual({
      readers: [],
      failures: [{ label: "@acme/core@>=0.2.19", error: expect.stringContaining("HTTP 503") }],
    });
  });
});

describe("sourceReader", () => {
  it("loads the reader a harness checkout carries in source", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "penguin-reader-src-"));
    try {
      const file = path.join(dir, "index-document.ts");
      fs.writeFileSync(
        file,
        "export function parsePluginIndex(data: unknown): unknown {\n  return { main: data };\n}\n",
      );
      const reader = await sourceReader("penguin-harness main", file);
      expect(reader.label).toBe("penguin-harness main");
      expect(reader.parse([1], "index.json")).toEqual({ main: [1] });
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
