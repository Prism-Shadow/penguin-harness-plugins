/** Static counterpart of PenguinHarness's PluginLibraryModule resource slots.
 * Only modules reached from ifaces.json#plugin contribute. Never import an installed package. */
import fs from "node:fs";
import path from "node:path";
import { parse } from "yaml";

type Row = Record<string, unknown>;
function row(value: unknown): Row {
  if (value === null || typeof value !== "object" || Array.isArray(value))
    throw new Error("expected an object");
  return value as Row;
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error("expected an array");
  return value;
}

/** Resolve files and directories within the actual package, including symlink targets. */
function inside(root: string, rel: unknown): string {
  if (
    typeof rel !== "string" ||
    /^[A-Za-z]:|^[/\\]/.test(rel) ||
    rel.split(/[/\\]/).some((p) => !p || p === "..")
  )
    throw new Error(`invalid package-relative path: ${String(rel)}`);
  const target = fs.realpathSync(path.resolve(root, rel));
  if (!target.startsWith(fs.realpathSync(root) + path.sep))
    throw new Error(`path leaves its package: ${rel}`);
  return target;
}
function file(root: string, rel: unknown): string {
  const name = inside(root, rel);
  if (!fs.statSync(name).isFile()) throw new Error(`${String(rel)} is not a file`);
  return name;
}
const json = (root: string, rel: string) =>
  row(JSON.parse(fs.readFileSync(file(root, rel), "utf8")));

const dated = /^\d{4}\.\d{2}\.\d{2}\.\d+$/;

function skill(root: string): void {
  const content = fs.readFileSync(file(root, "SKILL.md"), "utf8").replace(/^\uFEFF/, "");
  const block = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(content);
  if (!block) throw new Error("SKILL.md needs YAML frontmatter");
  const data = row(parse(block[1]!));
  if (typeof data.name !== "string" || !data.name.trim() || !dated.test(String(data.version)))
    throw new Error("SKILL.md needs a name and a dated version");
}

function workbench(root: string): void {
  file(root, "index.ts");
  const penguin = row(json(root, "package.json").penguin);
  if (typeof penguin.version !== "string" || !dated.test(penguin.version))
    throw new Error("workbench package.json needs a dated penguin.version");
  if (penguin.appCenter && row(penguin.appCenter).icon) file(root, row(penguin.appCenter).icon);
  const modules = list(penguin.modules).map(row);
  if (
    !modules.some(
      (m) =>
        m.name === "Workbench" &&
        Object.values(row(m.provides)).includes("@prismshadow/penguin-server#WorkbenchMain"),
    )
  )
    throw new Error("workbench package.json must declare a Workbench providing WorkbenchMain");
  for (const module of modules) {
    const tabs = list(row(module.contributes ?? {})["WebModule.sessionTabs"] ?? []);
    for (const tab of tabs) {
      const renderer = row(row(tab).renderer);
      if (renderer.iframe === undefined) continue;
      const src = row(renderer.iframe).src;
      if (typeof src !== "string" || !src.startsWith("ui/"))
        throw new Error("iframe source must be inside ui/");
      file(root, src);
    }
  }
}

function hookPackage(root: string, entry: unknown): void {
  const resource = row(entry);
  if (
    typeof resource.id !== "string" ||
    !resource.id.trim() ||
    typeof resource.version !== "string" ||
    !dated.test(resource.version)
  )
    throw new Error("hook package needs an id and a dated version");
  const dir = inside(root, resource.path);
  let commands = 0;
  for (const point of ["stop", "pre_tool_use", "user_prompt"]) {
    for (const item of list(resource[point] ?? [])) {
      const command = row(item);
      file(dir, command.command);
      if (
        command.timeout !== undefined &&
        (typeof command.timeout !== "number" || command.timeout <= 0)
      )
        throw new Error("hook timeout must be positive");
      if (
        command.trigger !== undefined &&
        command.trigger !== "prompt" &&
        command.trigger !== "host"
      )
        throw new Error("invalid hook trigger");
      commands++;
    }
  }
  if (!commands) throw new Error("hook package needs a command at a supported hook point");
}

export function probeLibrary(root: string): string[] {
  try {
    const table = json(root, "ifaces.json");
    row(table.ifaces);
    row(table.types);
    const modules = row(table.modules);
    const plugin = row(table.plugin);
    const pending = [...list(plugin.modules ?? []), ...list(plugin.replaces ?? [])];
    const visited = new Set<string>();
    let resources = 0;
    let hasHooks = false;
    while (pending.length) {
      const name = pending.pop();
      if (typeof name !== "string") throw new Error("invalid registered module name");
      if (visited.has(name)) continue;
      visited.add(name);
      const module = row(modules[name]);
      if (
        module.name !== name ||
        (module.kind !== undefined && module.kind !== "component" && module.kind !== "module")
      )
        throw new Error(`invalid registered module: ${name}`);
      for (const child of list(module.children ?? []))
        pending.push(typeof child === "string" ? child : row(child).keyed);
      const contributions = row(module.contributes ?? {});
      for (const hooks of list(contributions["PluginLibraryModule.hooks"] ?? [])) {
        if (hasHooks) throw new Error("only one hook package may be registered");
        hookPackage(root, hooks);
        hasHooks = true;
        resources++;
      }
      for (const kind of ["skills", "workbenches"]) {
        for (const entry of list(contributions[`PluginLibraryModule.${kind}`] ?? [])) {
          const resource = row(entry);
          if (
            typeof resource.id !== "string" ||
            !resource.id.trim() ||
            typeof resource.name !== "string" ||
            !(kind === "skills" ? /^[\w-]+$/ : /^[A-Za-z0-9][\w-]{0,63}$/).test(resource.name) ||
            typeof resource.path !== "string" ||
            path.posix.basename(resource.path) !== resource.name
          )
            throw new Error(`invalid ${kind} resource name or path`);
          const dir = inside(root, resource.path);
          if (kind === "skills") skill(dir);
          else workbench(dir);
          resources++;
        }
      }
    }
    if (!resources) return ["ifaces.json declares no reachable Skills, hooks or workbenches"];
    return [];
  } catch (err) {
    return [
      `invalid library package: ${err instanceof Error ? err.message.replaceAll(root + path.sep, "") : err}`,
    ];
  }
}
