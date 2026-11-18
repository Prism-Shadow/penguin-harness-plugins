import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { probePackage } from "./npm.js";

let root: string;
function put(rel: string, value: string | object) {
  const target = path.join(root, rel);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, typeof value === "string" ? value : JSON.stringify(value));
}
const board = { id: "game.board", name: "board", path: "workbenches/board" };
const skill = { id: "game.skill", name: "study", path: "skills/study" };
function table(contributes: Record<string, unknown>, names = ["Library"]) {
  put("ifaces.json", {
    ifaces: {},
    types: {},
    plugin: { modules: names, replaces: [] },
    modules: {
      Library: { name: "Library", kind: "component", children: [{ keyed: "Content" }] },
      Content: { name: "Content", kind: "component", contributes },
    },
  });
}
beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "catalog-library-"));
  put("package.json", { name: "game", version: "1.0.0" });
  put("workbenches/board/index.ts", "export default {};");
  put("workbenches/board/package.json", {
    penguin: {
      version: "2026.11.19.1",
      modules: [
        {
          name: "Workbench",
          provides: { main: "@prismshadow/penguin-server#WorkbenchMain" },
          contributes: {
            "WebModule.sessionTabs": [{ renderer: { iframe: { src: "ui/index.html" } } }],
          },
        },
      ],
    },
  });
  put("workbenches/board/ui/index.html", "<title>Board</title>");
  put("skills/study/SKILL.md", "---\nname: study\nversion: 2026.11.19.1\n---\n# Study");
  table({ "PluginLibraryModule.workbenches": [board], "PluginLibraryModule.skills": [skill] });
});
afterEach(() => fs.rmSync(root, { recursive: true, force: true }));
describe("resource-only packages", () => {
  it("checks the resources reached through registered children without loading their code", () => {
    expect(probePackage(root)).toEqual([]);
  });
  it.each([
    "workbenches/board/index.ts",
    "workbenches/board/package.json",
    "workbenches/board/ui/index.html",
    "skills/study/SKILL.md",
  ])("rejects a missing %s", (rel) => {
    fs.unlinkSync(path.join(root, rel));
    expect(probePackage(root).join(" ")).toContain(path.basename(rel));
  });
  it("rejects a Skill file without its identifying frontmatter", () => {
    put("skills/study/SKILL.md", "# A Markdown file");
    expect(probePackage(root).join(" ")).toContain("frontmatter");
  });

  it("refuses card metadata, unregistered modules and out-of-package resources", () => {
    table({ "PluginLibraryModule.cards": [{ id: "only" }] });
    expect(probePackage(root)).not.toEqual([]);
    table({ "PluginLibraryModule.workbenches": [board] }, []);
    expect(probePackage(root)).not.toEqual([]);
    table({ "PluginLibraryModule.workbenches": [{ ...board, path: "../board" }] });
    expect(probePackage(root)).not.toEqual([]);
  });
  it("preserves the code-plugin check when a JavaScript entry is declared", () => {
    put("package.json", { type: "module", main: "index.js" });
    put("index.js", "export const unrelated = 1;");
    expect(probePackage(root).join(" ")).toContain("activate");
    put("index.js", "export function activate() {}");
    expect(probePackage(root)).toEqual([]);
  });
});
