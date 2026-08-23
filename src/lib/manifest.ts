/**
 * A submission is one TOML file per plugin under `plugins/`, and this module is the
 * only thing that decides whether such a file is well-formed.
 *
 * The file path IS the npm specifier (`plugins/@scope/pkg.toml` ⇒ `@scope/pkg`), so a
 * rename is visible as a file move and two submissions can never quietly claim the
 * same package. One manifest carries exactly one version — the current one. Older
 * versions stay resolvable through npm; the index is a catalogue of what we recommend
 * installing today, not an archive of everything ever published.
 *
 * Validation collects every problem before throwing, because a contributor should see
 * the whole list once instead of discovering it one CI run at a time.
 */
import path from "node:path";
import { parse as parseToml } from "smol-toml";
import semver from "semver";
import { checkAuthor } from "./author.js";
import { checkLicense } from "./license.js";
import {
  CATEGORIES,
  MAX_CATEGORIES,
  MAX_DESCRIPTION_LENGTH,
  MAX_KEYWORDS,
  MAX_KEYWORD_LENGTH,
  NAME_PREFIX,
} from "./vocabulary.js";

export interface Manifest {
  /** npm specifier — the string an operator puts into `plugins.json`. */
  name: string;
  /** The version this index recommends; exact, never a range. */
  version: string;
  description: string;
  authors: string[];
  /** SPDX expression, restricted to the list in docs/licensing.md. */
  license: string;
  /** Source repository; required, because an unreadable plugin is not reviewable. */
  repository: string;
  homepage?: string;
  keywords?: string[];
  categories: string[];
  /** GitHub handles allowed to change this manifest. */
  owners: string[];
  /** A yanked plugin keeps its manifest as a record but is dropped from index.json. */
  yanked?: boolean;
  yankedReason?: string;
}

const KNOWN_KEYS = new Set([
  "name",
  "version",
  "description",
  "authors",
  "license",
  "repository",
  "homepage",
  "keywords",
  "categories",
  "owners",
  "yanked",
  "yanked_reason",
]);

const KEYWORD = /^[a-z0-9][a-z0-9-]*$/;
const GITHUB_HANDLE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;
const SCOPED_NAME = /^@[a-z0-9][a-z0-9-._]*\/[a-z0-9][a-z0-9-._]*$/;
const PLAIN_NAME = /^[a-z0-9][a-z0-9-._]*$/;

/** Thrown with every problem found in one manifest, so CI logs read as a checklist. */
export class ManifestError extends Error {
  constructor(
    readonly file: string,
    readonly problems: string[],
  ) {
    super([`${file}:`, ...problems.map((p) => `  - ${p}`)].join("\n"));
    this.name = "ManifestError";
  }
}

/** `plugins/@scope/pkg.toml` ⇒ `@scope/pkg`; returns null for anything else. */
export function nameFromManifestPath(file: string): string | null {
  const posix = file.split(path.sep).join("/");
  const match = /^plugins\/(.+)\.toml$/.exec(posix);
  if (match === null) return null;
  const name = match[1] as string;
  return isPluginName(name) ? name : null;
}

/** `@scope/pkg` ⇒ `plugins/@scope/pkg.toml`, the one place its manifest may live. */
export function manifestPathFor(name: string): string {
  return `plugins/${name}.toml`;
}

function isPluginName(name: string): boolean {
  if (name.length > 214) return false;
  return name.startsWith("@") ? SCOPED_NAME.test(name) : PLAIN_NAME.test(name);
}

function unscoped(name: string): string {
  const slash = name.indexOf("/");
  return slash === -1 ? name : name.slice(slash + 1);
}

function asStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((v) => typeof v === "string") ? (value as string[]) : null;
}

/**
 * Parses and fully validates one manifest. `file` is used both for error messages and
 * for the path/name agreement check, so callers must pass the repo-relative path.
 */
export function parseManifest(source: string, file: string): Manifest {
  const problems: string[] = [];
  let raw: Record<string, unknown>;
  try {
    raw = parseToml(source) as Record<string, unknown>;
  } catch (err) {
    throw new ManifestError(file, [`not valid TOML: ${err instanceof Error ? err.message : err}`]);
  }

  for (const key of Object.keys(raw)) {
    if (!KNOWN_KEYS.has(key)) problems.push(`unknown key "${key}"`);
  }

  const name = typeof raw.name === "string" ? raw.name : null;
  if (name === null) {
    problems.push(`"name" is required and must be a string`);
  } else if (!isPluginName(name)) {
    problems.push(`"${name}" is not a valid npm package name`);
  } else {
    const expected = manifestPathFor(name);
    if (file.split(path.sep).join("/") !== expected) {
      problems.push(`manifest for "${name}" must live at ${expected}`);
    }
    if (!unscoped(name).startsWith(NAME_PREFIX)) {
      problems.push(`package name must start with "${NAME_PREFIX}" after the scope`);
    }
  }

  const version = typeof raw.version === "string" ? raw.version : null;
  if (version === null) {
    problems.push(`"version" is required and must be a string`);
  } else if (semver.valid(version) === null) {
    problems.push(`"${version}" is not an exact semantic version`);
  }

  const description = typeof raw.description === "string" ? raw.description : null;
  if (description === null) {
    problems.push(`"description" is required and must be a string`);
  } else if (description.trim() === "") {
    problems.push(`"description" must not be empty`);
  } else if (description.length > MAX_DESCRIPTION_LENGTH) {
    problems.push(`"description" is longer than ${MAX_DESCRIPTION_LENGTH} characters`);
  }

  const authors = asStringArray(raw.authors);
  if (authors === null || authors.length === 0) {
    problems.push(`"authors" is required and must be a non-empty array of strings`);
  } else {
    for (const author of authors) {
      const problem = checkAuthor(author);
      if (problem !== null) problems.push(problem);
    }
  }

  const license = typeof raw.license === "string" ? raw.license : null;
  if (license === null) {
    problems.push(`"license" is required and must be a string`);
  } else {
    const problem = checkLicense(license);
    if (problem !== null) problems.push(problem);
  }

  const repository = typeof raw.repository === "string" ? raw.repository : null;
  if (repository === null) {
    problems.push(`"repository" is required and must be a string`);
  } else if (!repository.startsWith("https://")) {
    problems.push(`"repository" must be an https URL`);
  }

  if (raw.homepage !== undefined) {
    if (typeof raw.homepage !== "string" || !raw.homepage.startsWith("https://")) {
      problems.push(`"homepage" must be an https URL`);
    }
  }

  let keywords: string[] | undefined;
  if (raw.keywords !== undefined) {
    const list = asStringArray(raw.keywords);
    if (list === null) {
      problems.push(`"keywords" must be an array of strings`);
    } else {
      if (list.length > MAX_KEYWORDS) problems.push(`at most ${MAX_KEYWORDS} keywords`);
      for (const keyword of list) {
        if (!KEYWORD.test(keyword) || keyword.length > MAX_KEYWORD_LENGTH) {
          problems.push(
            `keyword "${keyword}" must be lowercase alphanumeric with hyphens, at most ${MAX_KEYWORD_LENGTH} characters`,
          );
        }
      }
      keywords = list;
    }
  }

  const categories = asStringArray(raw.categories);
  if (categories === null || categories.length === 0) {
    problems.push(`"categories" is required and must list at least one category`);
  } else {
    if (categories.length > MAX_CATEGORIES) {
      problems.push(`at most ${MAX_CATEGORIES} categories`);
    }
    if (new Set(categories).size !== categories.length) {
      problems.push(`"categories" must not repeat a category`);
    }
    for (const category of categories) {
      if (!(CATEGORIES as readonly string[]).includes(category)) {
        problems.push(`unknown category "${category}" (see docs/categories.md)`);
      }
    }
  }

  const owners = asStringArray(raw.owners);
  if (owners === null || owners.length === 0) {
    problems.push(`"owners" is required and must list at least one GitHub handle`);
  } else {
    for (const owner of owners) {
      if (!GITHUB_HANDLE.test(owner)) problems.push(`"${owner}" is not a valid GitHub handle`);
    }
  }

  let yanked: boolean | undefined;
  let yankedReason: string | undefined;
  if (raw.yanked !== undefined) {
    if (typeof raw.yanked !== "boolean") {
      problems.push(`"yanked" must be a boolean`);
    } else {
      yanked = raw.yanked;
    }
  }
  if (raw.yanked_reason !== undefined) {
    if (typeof raw.yanked_reason !== "string" || raw.yanked_reason.trim() === "") {
      problems.push(`"yanked_reason" must be a non-empty string`);
    } else {
      yankedReason = raw.yanked_reason;
    }
  }
  if (yanked === true && yankedReason === undefined) {
    problems.push(`a yanked plugin must say why in "yanked_reason"`);
  }
  if (yanked !== true && yankedReason !== undefined) {
    problems.push(`"yanked_reason" is only allowed together with yanked = true`);
  }

  if (problems.length > 0) throw new ManifestError(file, problems);

  return {
    name: name as string,
    version: version as string,
    description: description as string,
    authors: authors as string[],
    license: license as string,
    repository: repository as string,
    ...(typeof raw.homepage === "string" ? { homepage: raw.homepage } : {}),
    ...(keywords !== undefined ? { keywords } : {}),
    categories: categories as string[],
    owners: owners as string[],
    ...(yanked !== undefined ? { yanked } : {}),
    ...(yankedReason !== undefined ? { yankedReason } : {}),
  };
}
