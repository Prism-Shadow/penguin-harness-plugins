import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { packageIntegrity, type Manifest, type NpmRunner } from "@prismshadow/penguin-plugin-index";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  checkAgainstNpm,
  checkChannels,
  declaresActivate,
  entryPointOf,
  repositoryUrl,
} from "./npm.js";

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "npm-test-"));
afterAll(() => fs.rmSync(workDir, { recursive: true, force: true }));

const NAME = "@acme/widgets";
const VERSION = "1.2.3";
const LOCK = "{}";

const GOOD_PACKAGE_JSON = JSON.stringify({
  name: NAME,
  version: VERSION,
  type: "module",
  main: "./dist/index.js",
});

const GOOD = {
  "node_modules/@acme/widgets/package.json": GOOD_PACKAGE_JSON,
  "node_modules/@acme/widgets/dist/index.js": "export function activate(ctx) {}\n",
};

function write(root: string, files: Record<string, string>): void {
  for (const [name, content] of Object.entries(files)) {
    const full = path.join(root, ...name.split("/"));
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content);
  }
}

/** An npm that "installs" `files` into the prefix, as `npm ci` would lay them out. */
function fakeNpm(files: Record<string, string>): NpmRunner {
  return async (args, cwd) => {
    expect(args[0]).toBe("ci");
    write(cwd, files);
  };
}

/** The integrity `files` install to — computed the way a submitter's `pnpm new-entry` does. */
async function integrityOf(files: Record<string, string>): Promise<string> {
  const prefix = fs.mkdtempSync(path.join(workDir, "expected-"));
  write(prefix, files);
  return (await packageIntegrity(path.join(prefix, "node_modules/@acme/widgets"), prefix))
    .integrity;
}

let MANIFEST: Manifest;
beforeAll(async () => {
  MANIFEST = {
    name: NAME,
    version: VERSION,
    description: "Widget tools for PenguinHarness.",
    authors: ["Ada <@ada>"],
    license: "MIT",
    repository: "https://github.com/acme/widgets",
    categories: ["tool"],
    owners: ["ada"],
    integrity: `sha512-${Buffer.from("ab".repeat(64), "hex").toString("base64")}`,
    channels: [{ kind: "npm" }],
  };
});

function registry(overrides: Record<string, unknown> = {}): typeof fetch {
  const version = {
    version: VERSION,
    description: MANIFEST.description,
    license: MANIFEST.license,
    repository: { type: "git", url: `git+${MANIFEST.repository}.git` },
    ...overrides,
  };
  const packument = { name: NAME, versions: { [VERSION]: version } };
  return (async () => new Response(JSON.stringify(packument), { status: 200 })) as typeof fetch;
}

describe("entryPointOf", () => {
  it("prefers exports over main and falls back to index.js", () => {
    expect(entryPointOf({ version: "1", exports: { ".": { import: "./a.js" } } })).toBe("./a.js");
    expect(entryPointOf({ version: "1", exports: "./b.js", main: "./c.js" })).toBe("./b.js");
    expect(entryPointOf({ version: "1", main: "./c.js" })).toBe("./c.js");
    expect(entryPointOf({ version: "1" })).toBe("index.js");
  });
});

describe("repositoryUrl", () => {
  it("normalizes npm's repository shapes and shorthands to a bare https URL", () => {
    const want = "https://github.com/acme/widgets";
    for (const value of [
      "github:acme/widgets",
      "acme/widgets",
      "git://github.com/acme/widgets.git",
      { type: "git", url: "git+https://github.com/acme/widgets.git" },
      "https://github.com/acme/widgets/",
    ]) {
      expect(repositoryUrl(value), JSON.stringify(value)).toBe(want);
    }
  });
});

describe("declaresActivate", () => {
  it("recognises the shapes a bundler emits", () => {
    expect(declaresActivate("export function activate(ctx) {}")).toBe(true);
    expect(declaresActivate("export async function activate(ctx) {}")).toBe(true);
    expect(declaresActivate("const a = 1;\nexport { a as activate };")).toBe(true);
    expect(declaresActivate("exports.activate = activate;")).toBe(true);
    expect(declaresActivate("export function deactivate() {}")).toBe(false);
  });
});

describe("checkAgainstNpm", () => {
  it("passes an entry whose lock installs a loadable package", async () => {
    expect(
      await checkAgainstNpm(MANIFEST, LOCK, { fetchImpl: registry(), npm: fakeNpm(GOOD) }),
    ).toEqual([]);
  });

  it("counts a hoisted dependency as part of the package", async () => {
    const withDep = {
      ...GOOD,
      "node_modules/@acme/widgets/package.json": JSON.stringify({
        ...JSON.parse(GOOD_PACKAGE_JSON),
        dependencies: { left: "1.0.0" },
      }),
      "node_modules/left/package.json": JSON.stringify({ name: "left", version: "1.0.0" }),
    };
    expect(
      await checkAgainstNpm(MANIFEST, LOCK, { fetchImpl: registry(), npm: fakeNpm(withDep) }),
    ).toEqual([]);
  });

  it("reports an entry point that does not probe clean", async () => {
    const changed = {
      ...GOOD,
      "node_modules/@acme/widgets/dist/index.js": "export const x = 1;\n",
    };
    expect(
      await checkAgainstNpm(MANIFEST, LOCK, { fetchImpl: registry(), npm: fakeNpm(changed) }),
    ).toEqual([expect.stringContaining('declares no "activate" export')]);
  });

  it("reports a lock npm cannot install", async () => {
    const npm: NpmRunner = async () => {
      throw new Error("npm ci failed: lock out of sync");
    };
    expect(await checkAgainstNpm(MANIFEST, LOCK, { fetchImpl: registry(), npm })).toEqual([
      expect.stringContaining("installing from package-lock.json failed"),
    ]);
  });

  it("reports a missing activate export", async () => {
    const files = {
      ...GOOD,
      "node_modules/@acme/widgets/dist/index.js": "export function deactivate() {}\n",
    };
    const manifest = { ...MANIFEST, integrity: await integrityOf(files) };
    expect(
      await checkAgainstNpm(manifest, LOCK, { fetchImpl: registry(), npm: fakeNpm(files) }),
    ).toEqual([expect.stringContaining('declares no "activate" export')]);
  });

  it("reports a CommonJS package", async () => {
    const files = {
      "node_modules/@acme/widgets/package.json": JSON.stringify({
        name: NAME,
        main: "./dist/index.js",
      }),
      "node_modules/@acme/widgets/dist/index.js": "exports.activate = () => {};\n",
    };
    const manifest = { ...MANIFEST, integrity: await integrityOf(files) };
    expect(
      await checkAgainstNpm(manifest, LOCK, { fetchImpl: registry(), npm: fakeNpm(files) }),
    ).toEqual([expect.stringContaining('"type": "module"')]);
  });

  it("reports metadata drift against the published package", async () => {
    const problems = await checkAgainstNpm(MANIFEST, LOCK, {
      fetchImpl: registry({
        license: "Apache-2.0",
        description: "Something else.",
        deprecated: "moved",
      }),
      npm: fakeNpm(GOOD),
    });
    expect(problems).toEqual(
      expect.arrayContaining([
        expect.stringContaining("deprecated"),
        expect.stringContaining("license mismatch"),
        expect.stringContaining("description does not match"),
      ]),
    );
  });

  it("reports a version that is not published", async () => {
    const problems = await checkAgainstNpm({ ...MANIFEST, version: "9.9.9" }, LOCK, {
      fetchImpl: registry(),
      npm: fakeNpm(GOOD),
    });
    expect(problems).toEqual([expect.stringContaining("is not published on npm")]);
  });
});

describe("checkChannels", () => {
  const CHANNEL_URL = "https://github.com/acme/widgets/releases/download/v1.2.3/widgets-1.2.3.tgz";

  function serving(body: Buffer | number): typeof fetch {
    return (async () =>
      new Response(typeof body === "number" ? "" : body, {
        status: typeof body === "number" ? body : 200,
      })) as typeof fetch;
  }

  it("passes a channel whose tarball hashes to its integrity and installs to the entry's content", async () => {
    const files = GOOD;
    const tarball = Buffer.from("fake tarball bytes");
    const manifest = {
      ...MANIFEST,
      integrity: `sha512-${createHash("sha512").update(tarball).digest("base64")}`,
      channels: [{ kind: "github", url: CHANNEL_URL }],
    };
    const npm: NpmRunner = async (args, cwd) => {
      expect(args[0]).toBe("install");
      write(cwd, files);
    };
    expect(await checkChannels(manifest, { fetchImpl: serving(tarball), npm })).toEqual([]);
  });

  it("reports a tarball whose hash misses the channel's pin", async () => {
    const manifest = {
      ...MANIFEST,
      channels: [{ kind: "github", url: CHANNEL_URL }],
    };
    const problems = await checkChannels(manifest, {
      fetchImpl: serving(Buffer.from("x")),
      npm: async () => {},
    });
    expect(problems).toEqual([expect.stringContaining("the tarball's hash is")]);
  });

  it("reports a URL that does not answer", async () => {
    const manifest = {
      ...MANIFEST,
      channels: [{ kind: "github", url: CHANNEL_URL }],
    };
    const problems = await checkChannels(manifest, {
      fetchImpl: serving(404),
      npm: async () => {},
    });
    expect(problems).toEqual([expect.stringContaining("HTTP 404")]);
  });

  it("reports a tarball that installs to other content than the integrity names", async () => {
    const tarball = Buffer.from("fake tarball bytes");
    const manifest = {
      ...MANIFEST,
      integrity: `sha512-${createHash("sha512").update(Buffer.from("other bytes")).digest("base64")}`,
      channels: [{ kind: "github", url: CHANNEL_URL }],
    };
    const npm: NpmRunner = async (_args, cwd) => {
      write(cwd, GOOD);
    };
    const problems = await checkChannels(manifest, { fetchImpl: serving(tarball), npm });
    expect(problems).toEqual([expect.stringContaining("the tarball's hash is")]);
  });

  it("skips the npm channel — its checks live in checkAgainstNpm", async () => {
    const manifest = { ...MANIFEST, channels: [{ kind: "npm" }] };
    expect(await checkChannels(manifest, { fetchImpl: serving(404), npm: async () => {} })).toEqual(
      [],
    );
  });
});
