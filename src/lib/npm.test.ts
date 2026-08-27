import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import type { Manifest } from "./manifest.js";
import { checkAgainstNpm, declaresActivate, entryPointOf } from "./npm.js";

const MANIFEST: Manifest = {
  name: "@acme/penguin-extension-widgets",
  version: "1.2.3",
  description: "Widget tools for PenguinHarness.",
  authors: ["Ada <@ada>"],
  license: "MIT",
  repository: "https://github.com/acme/penguin-extension-widgets",
  categories: ["tool"],
  owners: ["ada"],
};

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "npm-test-"));
afterAll(() => fs.rmSync(workDir, { recursive: true, force: true }));

/** Builds a real .tgz in npm's layout so the probe exercises its actual unpack path. */
function tarball(files: Record<string, string>): Buffer {
  const dir = fs.mkdtempSync(path.join(workDir, "pkg-"));
  for (const [name, content] of Object.entries(files)) {
    const full = path.join(dir, "package", name);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content);
  }
  const archive = path.join(dir, "out.tgz");
  execFileSync("tar", ["-czf", archive, "-C", dir, "package"]);
  return fs.readFileSync(archive);
}

const GOOD_PACKAGE_JSON = JSON.stringify({
  name: MANIFEST.name,
  version: MANIFEST.version,
  type: "module",
  main: "./dist/index.js",
});

function registry(pkgFiles: Record<string, string>, overrides: Record<string, unknown> = {}) {
  const buffer = tarball(pkgFiles);
  const integrity = `sha512-${crypto.createHash("sha512").update(buffer).digest("base64")}`;
  const version = {
    version: MANIFEST.version,
    description: MANIFEST.description,
    license: MANIFEST.license,
    repository: { type: "git", url: `git+${MANIFEST.repository}.git` },
    type: "module",
    main: "./dist/index.js",
    dist: { tarball: "https://registry.test/pkg.tgz", integrity },
    ...overrides,
  };
  const packument = { name: MANIFEST.name, versions: { [MANIFEST.version]: version } };
  return (async (input: Parameters<typeof fetch>[0]) => {
    const url = String(input);
    if (url.endsWith(".tgz")) {
      return new Response(new Uint8Array(buffer), { status: 200 });
    }
    return new Response(JSON.stringify(packument), { status: 200 });
  }) as typeof fetch;
}

describe("entryPointOf", () => {
  it("prefers exports over main and falls back to index.js", () => {
    expect(entryPointOf({ version: "1", exports: { ".": { import: "./a.js" } } })).toBe("./a.js");
    expect(entryPointOf({ version: "1", exports: "./b.js", main: "./c.js" })).toBe("./b.js");
    expect(entryPointOf({ version: "1", main: "./c.js" })).toBe("./c.js");
    expect(entryPointOf({ version: "1" })).toBe("index.js");
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
  it("passes a package that matches its manifest", async () => {
    const fetchImpl = registry({
      "package.json": GOOD_PACKAGE_JSON,
      "dist/index.js": "export function activate(ctx) {}\n",
    });
    expect(await checkAgainstNpm(MANIFEST, fetchImpl)).toEqual([]);
  });

  it("reports a missing activate export", async () => {
    const fetchImpl = registry({
      "package.json": GOOD_PACKAGE_JSON,
      "dist/index.js": "export function deactivate() {}\n",
    });
    expect(await checkAgainstNpm(MANIFEST, fetchImpl)).toEqual([
      expect.stringContaining('declares no "activate" export'),
    ]);
  });

  it("reports a CommonJS package", async () => {
    const fetchImpl = registry({
      "package.json": JSON.stringify({ name: MANIFEST.name, main: "./dist/index.js" }),
      "dist/index.js": "exports.activate = () => {};\n",
    });
    expect(await checkAgainstNpm(MANIFEST, fetchImpl)).toEqual([
      expect.stringContaining('"type": "module"'),
    ]);
  });

  it("reports metadata drift against the published package", async () => {
    const fetchImpl = registry(
      { "package.json": GOOD_PACKAGE_JSON, "dist/index.js": "export function activate() {}\n" },
      { license: "Apache-2.0", description: "Something else.", deprecated: "moved" },
    );
    const problems = await checkAgainstNpm(MANIFEST, fetchImpl);
    expect(problems).toEqual(
      expect.arrayContaining([
        expect.stringContaining("deprecated"),
        expect.stringContaining("license mismatch"),
        expect.stringContaining("description does not match"),
      ]),
    );
  });

  it("reports a version that is not published", async () => {
    const fetchImpl = registry({
      "package.json": GOOD_PACKAGE_JSON,
      "dist/index.js": "export function activate() {}\n",
    });
    const problems = await checkAgainstNpm({ ...MANIFEST, version: "9.9.9" }, fetchImpl);
    expect(problems).toEqual([expect.stringContaining("is not published on npm")]);
  });

  it("reports a tarball that does not match its integrity hash", async () => {
    const fetchImpl = registry(
      { "package.json": GOOD_PACKAGE_JSON, "dist/index.js": "export function activate() {}\n" },
      { dist: { tarball: "https://registry.test/pkg.tgz", integrity: "sha512-bogus" } },
    );
    expect(await checkAgainstNpm(MANIFEST, fetchImpl)).toContainEqual(
      expect.stringContaining("does not match its sha512 integrity hash"),
    );
  });
});
