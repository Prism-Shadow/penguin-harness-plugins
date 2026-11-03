import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { checkLock, INSTALL_FLAGS, installFromLock, LOCK_ROOT_NAME } from "./lock.js";

const workDir = fs.mkdtempSync(path.join(os.tmpdir(), "lock-test-"));
afterAll(() => fs.rmSync(workDir, { recursive: true, force: true }));

function lock(rootDeps: Record<string, string>, installed: string, version = 3): string {
  return JSON.stringify({
    name: LOCK_ROOT_NAME,
    version: "0.0.0",
    lockfileVersion: version,
    requires: true,
    packages: {
      "": { name: LOCK_ROOT_NAME, version: "0.0.0", dependencies: rootDeps },
      "node_modules/@acme/widgets": { version: installed },
    },
  });
}

describe("checkLock", () => {
  it("accepts a lock of exactly this package at this version", () => {
    expect(
      checkLock(lock({ "@acme/widgets": "1.2.3" }, "1.2.3"), "@acme/widgets", "1.2.3"),
    ).toEqual([]);
  });

  it("refuses a range, another root dependency, another installed version, an old format", () => {
    const problems = (source: string) => checkLock(source, "@acme/widgets", "1.2.3");
    expect(problems(lock({ "@acme/widgets": "^1.2.3" }, "1.2.3"))).toEqual([
      expect.stringContaining("must depend on exactly @acme/widgets at 1.2.3"),
    ]);
    expect(problems(lock({ "@acme/widgets": "1.2.3", other: "1.0.0" }, "1.2.3"))).toEqual([
      expect.stringContaining("must depend on exactly"),
    ]);
    expect(problems(lock({ "@acme/widgets": "1.2.3" }, "1.2.4"))).toEqual([
      expect.stringContaining("does not install @acme/widgets@1.2.3"),
    ]);
    expect(problems(lock({ "@acme/widgets": "1.2.3" }, "1.2.3", 1))).toEqual([
      expect.stringContaining("lockfileVersion 2 or 3"),
    ]);
    expect(problems("{")).toEqual([expect.stringContaining("not valid JSON")]);
  });
});

describe("installFromLock", () => {
  it("installs the lock with npm ci and the fixed flags, into a prefix naming the one package", async () => {
    const prefix = fs.mkdtempSync(path.join(workDir, "prefix-"));
    const source = lock({ "@acme/widgets": "1.2.3" }, "1.2.3");
    const calls: string[][] = [];
    const pkgDir = await installFromLock(prefix, "@acme/widgets", "1.2.3", source, async (args) => {
      calls.push([...args]);
    });
    expect(calls).toEqual([["ci", ...INSTALL_FLAGS]]);
    expect(INSTALL_FLAGS).toEqual(expect.arrayContaining(["--ignore-scripts", "--force"]));
    expect(pkgDir).toBe(path.join(prefix, "node_modules", "@acme", "widgets"));
    expect(fs.readFileSync(path.join(prefix, "package-lock.json"), "utf8")).toBe(source);
    expect(JSON.parse(fs.readFileSync(path.join(prefix, "package.json"), "utf8"))).toMatchObject({
      dependencies: { "@acme/widgets": "1.2.3" },
    });
  });
});
