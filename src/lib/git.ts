/**
 * The repository's own history is metadata: it tells us when an entry last changed
 * (which becomes `updatedAt` in the index, as in typst/packages) and which entries a pull
 * request touches (which is what the expensive npm checks are scoped to, as in zed's
 * `changedExtensionIds`).
 *
 * All of it needs full history, so CI must check out with `fetch-depth: 0`.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import {
  LOCK_FILE,
  MANIFEST_FILE,
  PACKAGE_DIR,
  parseEntryPath,
  PLUGINS_DIR,
  STORED_FILE,
} from "@prismshadow/penguin-plugin-index";

/**
 * Runs git and returns stdout. stderr is captured rather than inherited: several callers
 * ask questions whose answer is "no" (does this ref exist? did this file exist then?),
 * and git's complaint about it is not the contributor's problem.
 */
function git(args: string[]): string {
  return execFileSync("git", args, {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

/** The files an entry directory may hold in this repository. */
const ENTRY_FILES = new Set([MANIFEST_FILE, LOCK_FILE]);

/**
 * Every file under `plugins/`, repo-relative and sorted — except what the repository
 * ignores in an entry (`package/`, `.stored`), so a store-shaped checkout lists the same.
 */
function pluginTreeFiles(root: string): string[] {
  const dir = path.join(root, PLUGINS_DIR);
  if (!fs.existsSync(dir)) return [];
  const files: string[] = [];
  const walk = (current: string): void => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== PACKAGE_DIR) walk(full);
      } else if (entry.name !== STORED_FILE) {
        files.push(path.relative(root, full).split(path.sep).join("/"));
      }
    }
  };
  walk(dir);
  return files.sort();
}

/** Every manifest in the working tree, repo-relative and sorted. */
export function listManifestFiles(root: string): string[] {
  return pluginTreeFiles(root).filter((file) => path.posix.basename(file) === MANIFEST_FILE);
}

/**
 * Files under `plugins/` that are not an entry's manifest or lock: a stray file, or an
 * entry file at the wrong depth. The manifest check cannot see these, so they are listed
 * on their own.
 */
export function strayFiles(root: string): string[] {
  return pluginTreeFiles(root).filter((file) => {
    const entry = parseEntryPath(file.slice(PLUGINS_DIR.length + 1));
    return entry === null || !ENTRY_FILES.has(entry.file);
  });
}

/** An entry's directory (`plugins/<name>/<version>/<key>`) for a file in it, or null. */
export function entryDirOf(file: string): string | null {
  if (!file.startsWith(`${PLUGINS_DIR}/`)) return null;
  const entry = parseEntryPath(file.slice(PLUGINS_DIR.length + 1));
  if (entry === null) return null;
  return `${PLUGINS_DIR}/${entry.name}/${entry.version}/${entry.key}`;
}

/**
 * Commit time of the last change to `file`, in Unix seconds. Falls back to the file's
 * mtime so a shallow clone or a dirty working tree still produces an index.
 */
export function lastCommitTimestamp(file: string): number {
  try {
    const out = git(["--no-pager", "log", "-1", "--format=%ct", "--", file]).trim();
    const seconds = Number.parseInt(out, 10);
    if (Number.isFinite(seconds) && seconds > 0) return seconds;
  } catch {
    // Fall through to the filesystem.
  }
  return Math.floor(fs.statSync(file).mtimeMs / 1000);
}

export type ChangeStatus = "added" | "modified" | "deleted" | "renamed";

/** A change to one entry, named by its manifest's path. */
export interface EntryChange {
  status: ChangeStatus;
  /** The entry's manifest: `plugins/<name>/<version>/<key>/manifest.toml`. */
  file: string;
  /** Only set for renames: where the manifest used to live. */
  from?: string;
}

/** Whether `ref` resolves in this clone — false in a fork checkout without the base. */
export function hasRef(ref: string): boolean {
  try {
    git(["rev-parse", "--verify", "--quiet", `${ref}^{commit}`]);
    return true;
  } catch {
    return false;
  }
}

/**
 * Entries changed between the merge base with `baseRef` and the working tree, one change
 * per entry: its manifest's own status when the manifest changed, else `modified` (only
 * its lock changed).
 *
 * Untracked files count as additions. CI only ever sees committed work, but a
 * contributor running `pnpm validate` before committing would otherwise be told their
 * brand-new entry changes nothing — the one moment the change-level rules are most
 * worth hearing.
 */
export function changedEntries(baseRef: string): EntryChange[] {
  const mergeBase = git(["merge-base", "HEAD", baseRef]).trim();
  const byEntry = new Map<string, EntryChange>();
  const note = (status: ChangeStatus, file: string, from?: string) => {
    const dir = entryDirOf(file);
    if (dir === null) return;
    const manifest = `${dir}/${MANIFEST_FILE}`;
    const own = path.posix.basename(file) === MANIFEST_FILE;
    const known = byEntry.get(manifest);
    // The manifest's own status says what happened to the entry; a lock change alone is
    // an edit of the entry.
    if (own) {
      byEntry.set(manifest, { status, file: manifest, ...(from !== undefined ? { from } : {}) });
    } else if (known === undefined) {
      byEntry.set(manifest, { status: "modified", file: manifest });
    }
  };
  for (const file of git(["ls-files", "--others", "--exclude-standard", "--", `${PLUGINS_DIR}/`])
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")) {
    note("added", file);
  }
  const out = git(["diff", "--name-status", "-M", mergeBase, "--", `${PLUGINS_DIR}/`]);
  for (const line of out.split("\n")) {
    if (line.trim() === "") continue;
    const parts = line.split("\t");
    const code = parts[0] as string;
    if (code.startsWith("R")) {
      note("renamed", parts[2] as string, parts[1] as string);
      continue;
    }
    const file = parts[1] as string;
    if (code.startsWith("A")) note("added", file);
    else if (code.startsWith("D")) note("deleted", file);
    else note("modified", file);
  }
  return [...byEntry.values()].sort((a, b) => (a.file < b.file ? -1 : a.file > b.file ? 1 : 0));
}

/** File contents at a ref, or null when the file did not exist there. */
export function readAtRef(ref: string, file: string): string | null {
  try {
    return git(["--no-pager", "show", `${ref}:${file}`]);
  } catch {
    return null;
  }
}

/** Manifests under `plugins/<name>/` at a ref: the other entries of one name. */
export function manifestsAtRef(ref: string, name: string): string[] {
  try {
    return git(["ls-tree", "-r", "--name-only", ref, "--", `${PLUGINS_DIR}/${name}/`])
      .split("\n")
      .filter((file) => path.posix.basename(file) === MANIFEST_FILE);
  } catch {
    return [];
  }
}

/** Merge base of HEAD and `baseRef`, for reading the pre-PR state of a manifest. */
export function mergeBase(baseRef: string): string {
  return git(["merge-base", "HEAD", baseRef]).trim();
}
