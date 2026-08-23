/**
 * The repository's own history is metadata: it tells us when a plugin entry last
 * changed (which becomes `updatedAt` in the index, as in typst/packages) and which
 * manifests a pull request touches (which is what the expensive npm checks are scoped
 * to, as in zed's `changedExtensionIds`).
 *
 * All of it needs full history, so CI must check out with `fetch-depth: 0`.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

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

/** Every manifest in the working tree, repo-relative and sorted. */
export function listManifestFiles(root: string): string[] {
  const dir = path.join(root, "plugins");
  if (!fs.existsSync(dir)) return [];
  const files: string[] = [];
  const walk = (current: string): void => {
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile() && entry.name.endsWith(".toml")) {
        files.push(path.relative(root, full).split(path.sep).join("/"));
      }
    }
  };
  walk(dir);
  return files.sort();
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

export interface ManifestChange {
  status: ChangeStatus;
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
 * Manifests changed between the merge base with `baseRef` and the working tree.
 *
 * Untracked files count as additions. CI only ever sees committed work, but a
 * contributor running `pnpm validate` before committing would otherwise be told their
 * brand-new manifest changes nothing — the one moment the change-level rules are most
 * worth hearing.
 */
export function changedManifests(baseRef: string): ManifestChange[] {
  const mergeBase = git(["merge-base", "HEAD", baseRef]).trim();
  const out = git(["diff", "--name-status", "-M", mergeBase, "--", "plugins/"]);
  const changes: ManifestChange[] = [];
  for (const file of git(["ls-files", "--others", "--exclude-standard", "--", "plugins/"])
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.endsWith(".toml"))) {
    changes.push({ status: "added", file });
  }
  for (const line of out.split("\n")) {
    if (line.trim() === "") continue;
    const parts = line.split("\t");
    const code = parts[0] as string;
    if (code.startsWith("R")) {
      changes.push({ status: "renamed", from: parts[1] as string, file: parts[2] as string });
      continue;
    }
    const file = parts[1] as string;
    if (!file.endsWith(".toml")) continue;
    if (code.startsWith("A")) changes.push({ status: "added", file });
    else if (code.startsWith("D")) changes.push({ status: "deleted", file });
    else changes.push({ status: "modified", file });
  }
  return changes;
}

/** File contents at a ref, or null when the file did not exist there. */
export function readAtRef(ref: string, file: string): string | null {
  try {
    return git(["--no-pager", "show", `${ref}:${file}`]);
  } catch {
    return null;
  }
}

/** Merge base of HEAD and `baseRef`, for reading the pre-PR state of a manifest. */
export function mergeBase(baseRef: string): string {
  return git(["merge-base", "HEAD", baseRef]).trim();
}
