import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import {
  bucketOf,
  copyPackage,
  entryDir,
  entryKey,
  nameDir,
  packageFiles,
  packageIntegrity,
  parseEntryPath,
} from "./entry.js";

const INTEGRITY = `sha256-${"fedcba9876543210".repeat(4)}`;

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "entry-test-"));
afterAll(() => fs.rmSync(workDir, { recursive: true, force: true }));

function write(root: string, files: Record<string, string>): void {
  for (const [name, content] of Object.entries(files)) {
    const full = path.join(root, ...name.split("/"));
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content);
  }
}

const pkg = (name: string, deps: Record<string, string> = {}) =>
  JSON.stringify({ name, version: "1.0.0", dependencies: deps });

describe("entry paths", () => {
  it("spell the bucket, the name, the version and the first 16 hex digits of the integrity", () => {
    expect(entryKey(INTEGRITY)).toBe("fedcba9876543210");
    expect(entryKey("sha512-abc")).toBeNull();
    expect(entryDir("@acme/widgets", "1.2.3", INTEGRITY)).toBe(
      "@acme/wi/dg/widgets/1.2.3/fedcba9876543210",
    );
    expect(() => entryDir("solo", "1.0.0", "sha256-XYZ")).toThrow("not a sha256 integrity");
  });

  it("file a name in the bucket its name spells", () => {
    // The same vectors run in PenguinHarness's plugin store tests: one rule, two copies.
    // There they carry the store's `packages/` root; here the root is the caller's.
    const vectors: Array<[string, string]> = [
      ["a", "1/a"],
      ["ab", "2/ab"],
      ["abc", "3/a/abc"],
      ["abcd", "ab/cd/abcd"],
      ["Sandbox-Bwrap", "sa/nd/Sandbox-Bwrap"],
      ["@penguinharness/x", "@penguinharness/1/x"],
      ["@penguinharness/fs", "@penguinharness/2/fs"],
      ["@penguinharness/git", "@penguinharness/3/g/git"],
      ["@penguinharness/sandbox-bwrap", "@penguinharness/sa/nd/sandbox-bwrap"],
    ];
    for (const [name, want] of vectors) expect(nameDir(name)).toBe(want);
    expect(bucketOf("@penguinharness/sandbox-bwrap")).toEqual(["sa", "nd"]);
  });

  it("read back, scoped or not, in a bucket of one level or two", () => {
    expect(parseEntryPath("@acme/wi/dg/widgets/1.2.3/fedcba9876543210/manifest.toml")).toEqual({
      name: "@acme/widgets",
      bucket: ["wi", "dg"],
      version: "1.2.3",
      key: "fedcba9876543210",
      file: "manifest.toml",
    });
    expect(parseEntryPath("2/fs/0.1.0/fedcba9876543210/package/dist/a.js")).toEqual({
      name: "fs",
      bucket: ["2"],
      version: "0.1.0",
      key: "fedcba9876543210",
      file: "package/dist/a.js",
    });
    expect(parseEntryPath("so/lo/solo/0.1.0/manifest.toml")).toBeNull();
  });
});

describe("an entry's package", () => {
  it("folds the dependencies npm hoisted out of it into its own node_modules", async () => {
    const prefix = path.join(workDir, "hoisted");
    write(prefix, {
      "node_modules/@acme/widgets/package.json": pkg("@acme/widgets", { left: "1", right: "1" }),
      "node_modules/@acme/widgets/index.js": "export {}\n",
      "node_modules/@acme/widgets/node_modules/right/package.json": pkg("right"),
      "node_modules/left/package.json": pkg("left", { deeper: "1" }),
      "node_modules/deeper/package.json": pkg("deeper"),
      "node_modules/unrelated/package.json": pkg("unrelated"),
    });
    const files = await packageFiles(path.join(prefix, "node_modules/@acme/widgets"), prefix);
    expect(files.map((f) => f.rel).sort()).toEqual([
      "package/index.js",
      "package/node_modules/deeper/package.json",
      "package/node_modules/left/package.json",
      "package/node_modules/right/package.json",
      "package/package.json",
    ]);
  });

  it("has the same integrity installed hoisted as it has stored nested", async () => {
    const prefix = path.join(workDir, "roundtrip");
    write(prefix, {
      "node_modules/solo/package.json": pkg("solo", { dep: "1" }),
      "node_modules/solo/index.js": "export {}\n",
      "node_modules/dep/package.json": pkg("dep"),
    });
    const pkgDir = path.join(prefix, "node_modules/solo");
    const installed = await packageIntegrity(pkgDir, prefix);
    const entry = path.join(workDir, "roundtrip-entry");
    await copyPackage(await packageFiles(pkgDir, prefix), entry);
    expect(fs.existsSync(path.join(entry, "package/node_modules/dep/package.json"))).toBe(true);
    expect(await packageIntegrity(path.join(entry, "package"))).toEqual(installed);
  });

  it("changes its integrity with any byte of any file", async () => {
    const dir = path.join(workDir, "bytes");
    write(dir, { "package.json": pkg("solo"), "index.js": "export {}\n" });
    const before = await packageIntegrity(dir);
    write(dir, { "index.js": "export {};\n" });
    expect((await packageIntegrity(dir)).integrity).not.toBe(before.integrity);
  });
});
