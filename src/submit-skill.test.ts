import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);

function readSkill(): string {
  return readFileSync(
    path.join(repoRoot, "skills/penguin-plugin-index/SKILL.md"),
    "utf8",
  );
}

function readContributing(): string {
  return readFileSync(path.join(repoRoot, "CONTRIBUTING.md"), "utf8");
}

describe("submitting with the skill", () => {
  it("drives the submission with one channel first — npm or a tarball URL, not npm as a given", () => {
    const skill = readSkill();
    expect(skill).toContain("one channel");
    expect(skill).toContain("[[channels]]");
    expect(skill).not.toMatch(/must already be published on npm/);
  });

  it("writes the entry through the repository's own new-entry command", () => {
    const skill = readSkill();
    const contributing = readContributing();
    expect(skill).toContain("pnpm new-entry <name>@<version>");
    // The command the skill names is the command the repository ships.
    expect(contributing).toContain("pnpm new-entry");
  });

  it("has the author fill in categories and owners — and never the integrity", () => {
    const skill = readSkill();
    expect(skill).toContain("categories");
    expect(skill).toContain("owners");
    expect(skill.toLowerCase()).toContain("never\n   edit `integrity`");
    expect(readContributing()).toContain("do not edit");
  });

  it("runs the repository's pre-PR checks locally before opening the pull request", () => {
    const skill = readSkill();
    const contributing = readContributing();
    expect(skill).toContain("pnpm validate && pnpm check-npm");
    // Those scripts exist in this repository and do what the skill promises.
    const pkg = JSON.parse(
      readFileSync(path.join(repoRoot, "package.json"), "utf8"),
    ) as Record<string, { validate?: string; "check-npm"?: string }>;
    expect(pkg.scripts.validate).toBeDefined();
    expect(pkg.scripts["check-npm"]).toBeDefined();
  });

  it("opens one pull request with exactly one entry", () => {
    const skill = readSkill();
    expect(skill).toContain("one pull request with exactly one entry");
    expect(readContributing()).toContain("One entry per pull request.");
  });

  it("treats CONTRIBUTING.md as the authority over the skill", () => {
    expect(readSkill()).toContain("CONTRIBUTING.md");
    expect(readSkill()).toMatch(/CONTRIBUTING\.md\)\s*\n\s*is the authority/);
    expect(readContributing()).toContain("this page wins");
  });
});
