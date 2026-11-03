import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { gunzipSync } from "node:zlib";
import { afterAll, describe, expect, it } from "vitest";
import { archiveIntegrity, filesUnder, packArchive, packTar, type ArchiveFile } from "./archive.js";

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "archive-test-"));
afterAll(() => fs.rmSync(workDir, { recursive: true, force: true }));

let seq = 0;
/** Writes `files` into a fresh directory; a name ending in `*` is made executable. */
function tree(files: Record<string, string>): string {
  seq += 1;
  const dir = path.join(workDir, `tree-${seq}`);
  for (const [name, content] of Object.entries(files)) {
    const exec = name.endsWith("*");
    const full = path.join(dir, ...name.replace(/\*$/, "").split("/"));
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content);
    fs.chmodSync(full, exec ? 0o700 : 0o600);
  }
  return dir;
}

const LONG = `node_modules/${"deep/".repeat(40)}file.js`;

const FILES = {
  "package.json": '{"name":"x","version":"1.0.0"}\n',
  "dist/index.js": "export function activate() {}\n",
  "vendor/x64/bin/tool*": "#!/bin/sh\n",
  [LONG]: "long\n",
  "λ/unicode.txt": "unicode\n",
};

/** GNU tar's verbose listing: mode, owner, size, date, path — per line. */
function listing(bytes: Buffer): string[] {
  const file = path.join(workDir, `listing-${(seq += 1)}.tar`);
  fs.writeFileSync(file, bytes);
  return execFileSync("tar", ["--numeric-owner", "--utc", "-tvf", file], { encoding: "utf8" })
    .trim()
    .split("\n");
}

describe("the archiver", () => {
  it("writes the same bytes for the same files, whatever their order, mtimes and owner modes", async () => {
    const a = await filesUnder(tree(FILES), "package");
    const bDir = tree(FILES);
    fs.utimesSync(path.join(bDir, "package.json"), new Date(2001, 1, 1), new Date(2001, 1, 1));
    const b = (await filesUnder(bDir, "package")).reverse();
    expect((await packTar(b)).equals(await packTar(a))).toBe(true);
    expect((await packArchive(b)).equals(await packArchive(a))).toBe(true);
    expect(gunzipSync(await packArchive(a)).equals(await packTar(a))).toBe(true);
  });

  it("is a tar that tar reads: sorted, epoch, root, 0755 for what executes and 0644 otherwise", async () => {
    const files = await filesUnder(tree(FILES), "package");
    const lines = listing(await packTar(files));
    expect(lines.map((l) => l.split(/\s+/).slice(5).join(" "))).toEqual([
      "package/dist/index.js",
      `package/${LONG}`,
      "package/package.json",
      "package/vendor/x64/bin/tool",
      "package/λ/unicode.txt",
    ]);
    for (const line of lines) {
      const [mode, owner, , date] = line.split(/\s+/);
      expect(owner).toBe("0/0");
      expect(date).toBe("1970-01-01");
      expect(mode).toBe(line.endsWith("/tool") ? "-rwxr-xr-x" : "-rw-r--r--");
    }
  });

  it("leaves symlinks out", async () => {
    const dir = tree({ "a.js": "a\n" });
    fs.mkdirSync(path.join(dir, ".bin"));
    fs.symlinkSync("../a.js", path.join(dir, ".bin", "a"));
    expect((await filesUnder(dir, "")).map((f) => f.rel)).toEqual(["a.js"]);
  });

  it("refuses a path an unpacker could escape with, and a path twice", async () => {
    const abs = path.join(tree({ "a.js": "a\n" }), "a.js");
    const bad: ArchiveFile[] = [{ rel: "package/../a.js", abs, exec: false }];
    await expect(packTar(bad)).rejects.toThrow("is not a relative posix path");
    const twice: ArchiveFile[] = [
      { rel: "package/a.js", abs, exec: false },
      { rel: "package/a.js", abs, exec: true },
    ];
    await expect(packTar(twice)).rejects.toThrow("appears twice");
  });

  it("pins the format: one known tree has one known integrity", async () => {
    const files = await filesUnder(tree(FILES), "package");
    const { integrity, size } = await archiveIntegrity(files);
    expect(size).toBe((await packTar(files)).byteLength);
    expect(integrity).toBe(GOLDEN);
  });
});

/**
 * The integrity of FILES. Changing it means every entry in every index and store changes
 * its key: a breaking release of this package, never a test to update in passing.
 */
const GOLDEN = "sha256-686e836725496c43a6a419146d198592f2e5bd653a00a68ae51b695b72cd0a2f";
