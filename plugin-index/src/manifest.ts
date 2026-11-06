/**
 * An entry's `manifest.toml`, and the only thing that decides whether one is well-formed.
 *
 * The manifest sits at `packages/[<@scope>/]<bucket>/<name>/<version>/manifest.toml`,
 * and the path is checked three ways: it spells the package's name, the bucket that name
 * files under (see the entry module), and its version. On the keyed form
 * (`…/<version>/<key>/manifest.toml`, for more than one content of one name and
 * version) the key is checked too: it must be the `integrity`'s digest in base64url. So
 * a new version is a new directory, a new content of one version is another directory
 * beside it, and nothing about an entry can change without the change showing up as a
 * move.
 *
 * Validation collects every problem before throwing, because a contributor should see
 * the whole list once instead of discovering it one CI run at a time.
 */
import { parse as parseToml } from "smol-toml";
import semver from "semver";
import { checkAuthor } from "./author.js";
import {
  bucketOf,
  entryKey,
  INTEGRITY,
  isPackageName,
  keylessEntryDir,
  MANIFEST_FILE,
  nameDir,
  parseEntryPath,
} from "./entry.js";
import { checkLicense } from "./license.js";
import {
  CATEGORIES,
  CHANNEL_NAME,
  MAX_CATEGORIES,
  MAX_CHANNELS,
  MAX_DESCRIPTION_LENGTH,
  MAX_KEYWORDS,
  MAX_KEYWORD_LENGTH,
} from "./vocabulary.js";

/** The directory of the index repository that holds the entries. */
export const PACKAGES_DIR = "packages";

export interface Manifest {
  /** npm specifier — the name an operator puts into a Project's `[plugins]` table. */
  name: string;
  /** Exact, never a range. */
  version: string;
  description: string;
  authors: string[];
  /** SPDX expression, restricted to the list in docs/licensing.md. */
  license: string;
  /** Source repository; required, because an unreadable plugin is not reviewable. */
  repository: string;
  homepage?: string;
  /** The plugin's card name, as the package's own manifest declares it — what an
   *  uninstalled plugin's card shows instead of the package name. Optional: an entry
   *  without one falls back to the package name. */
  title?: string;
  /** The card name in Chinese, beside `title`. */
  title_zh?: string;
  keywords?: string[];
  categories: string[];
  /** GitHub handles allowed to change this entry and to add entries under its name. */
  owners: string[];
  /** A yanked entry keeps its manifest as a record but is dropped from index.json. */
  yanked?: boolean;
  yankedReason?: string;
  /** npm's own integrity (`sha512-<base64>`, the registry's `dist.integrity`) of the one
   *  tarball the entry's channels serve — and, on a keyed path, the key. The release
   *  pipeline packs what npm publishes, so every channel can serve the very same bytes. */
  integrity: string;
  /**
   * Where the package can be installed from, each serving that one tarball. npm is a
   * channel like any other (`kind = "npm"`); an entry need not have it, but must have at
   * least one channel, because an installable-from-nowhere entry is not listable.
   */
  channels: Channel[];
}

/** One distribution channel: where the tarball lives. */
export interface Channel {
  /** Lowercase alphanumeric with hyphens, at most 20 characters. "npm" is the registry. */
  kind: string;
  /** `https://` URL of the tarball as the channel serves it. The npm channel has none —
   *  the registry serves `name` at the standard URL. */
  url?: string;
}

const KNOWN_KEYS = new Set([
  "name",
  "version",
  "description",
  "authors",
  "license",
  "repository",
  "homepage",
  "title",
  "title_zh",
  "keywords",
  "categories",
  "owners",
  "yanked",
  "yanked_reason",
  "integrity",
  "channels",
]);

const KEYWORD = /^[a-z0-9][a-z0-9-]*$/;
const GITHUB_HANDLE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;

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

/** `@scope/widgets`, `1.2.3` ⇒ `packages/@scope/wi/dg/widgets/1.2.3/manifest.toml`. */
export function manifestPathFor(name: string, version: string): string {
  return `${PACKAGES_DIR}/${keylessEntryDir(name, version)}/${MANIFEST_FILE}`;
}

function asStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((v) => typeof v === "string") ? (value as string[]) : null;
}

/**
 * Parses and fully validates one manifest. `file` is used both for error messages and
 * for the path agreement checks, so callers must pass the repo-relative posix path.
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
  } else if (!isPackageName(name)) {
    problems.push(`"${name}" is not a valid npm package name`);
  }

  const version = typeof raw.version === "string" ? raw.version : null;
  if (version === null) {
    problems.push(`"version" is required and must be a string`);
  } else if (semver.valid(version) !== version) {
    problems.push(`"${version}" is not an exact semantic version`);
  }

  const integrity = typeof raw.integrity === "string" ? raw.integrity : null;
  if (integrity === null) {
    problems.push(`"integrity" is required and must be a string`);
  } else if (!INTEGRITY.test(integrity)) {
    problems.push(`"integrity" must be npm's sha512- followed by 88 base64 characters`);
  }

  // The path spells the entry: its bucket, its name, its version and its content, each checked.
  if (name !== null && isPackageName(name)) {
    const posix = file.split("\\").join("/");
    const under = `${PACKAGES_DIR}/${nameDir(name)}/`;
    if (!posix.startsWith(under)) {
      const filed = posix.startsWith(`${PACKAGES_DIR}/`)
        ? parseEntryPath(posix.slice(PACKAGES_DIR.length + 1))
        : null;
      problems.push(
        filed !== null && filed.name === name
          ? `the path files "${name}" under bucket ${filed.bucket.join("/")}, its name spells ${bucketOf(name).join("/")}: it must live under ${under}`
          : `a manifest for "${name}" must live under ${under}`,
      );
    } else {
      const segments = posix.slice(under.length).split("/");
      const keyless = segments.length === 2;
      const [dirVersion, dirKey, leaf, ...rest] = segments;
      if ((keyless ? dirKey : leaf) !== MANIFEST_FILE || (!keyless && rest.length > 0)) {
        problems.push(
          `a manifest must be ${under}<version>/${MANIFEST_FILE} or ${under}<version>/<key>/${MANIFEST_FILE}`,
        );
      } else {
        if (version !== null && dirVersion !== version) {
          problems.push(`the path says version ${dirVersion}, the manifest says ${version}`);
        }
        if (!keyless) {
          const key = integrity === null ? null : entryKey(integrity);
          if (key !== null && dirKey !== key) {
            problems.push(
              `the path's key ${dirKey} is not "integrity"'s digest in base64url (${key})`,
            );
          }
        }
      }
    }
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

  for (const key of ["title", "title_zh"] as const) {
    if (raw[key] !== undefined) {
      if (typeof raw[key] !== "string" || (raw[key] as string).trim() === "") {
        problems.push(`"${key}" must be a non-empty string`);
      } else if ((raw[key] as string).length > MAX_DESCRIPTION_LENGTH) {
        problems.push(`"${key}" is longer than ${MAX_DESCRIPTION_LENGTH} characters`);
      }
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
    problems.push(`a yanked entry must say why in "yanked_reason"`);
  }
  if (yanked !== true && yankedReason !== undefined) {
    problems.push(`"yanked_reason" is only allowed together with yanked = true`);
  }

  let channels: Channel[] = [];
  {
    const rawChannels = raw.channels;
    if (!Array.isArray(rawChannels)) {
      problems.push(`"channels" is required and must list at least one [[channels]] table`);
    } else {
      if (rawChannels.length === 0) {
        problems.push(`"channels" must list at least one [[channels]] table`);
      }
      if (rawChannels.length > MAX_CHANNELS) {
        problems.push(`at most ${MAX_CHANNELS} channels`);
      }
      const seenKinds = new Set<string>();
      const seenUrls = new Set<string>();
      for (const [i, entry] of rawChannels.entries()) {
        if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
          problems.push(`channels[${i}] must be a table with "kind", "url" and "integrity"`);
          continue;
        }
        const table = entry as Record<string, unknown>;
        if (table.integrity !== undefined) {
          problems.push(
            `channels[${i}].integrity is not a thing: the entry has one integrity, the tarball's, and every channel serves it`,
          );
        }
        const kind = table.kind;
        if (typeof kind !== "string" || !CHANNEL_NAME.test(kind)) {
          problems.push(
            `channels[${i}].kind must be lowercase alphanumeric with hyphens, at most 20 characters`,
          );
        } else if (kind === "npm" && seenKinds.has(kind)) {
          problems.push(`channels[${i}].kind "npm" repeats an earlier channel`);
        } else if (seenKinds.has(kind)) {
          problems.push(`channels[${i}].kind "${kind}" repeats an earlier channel`);
        } else {
          seenKinds.add(kind);
        }
        const isNpm = kind === "npm";
        const url = table.url;
        const urlOk =
          isNpm === true
            ? url === undefined
            : typeof url === "string" && url.startsWith("https://");
        if (isNpm) {
          if (url !== undefined) {
            problems.push(`channels[${i}].url must be omitted for the "npm" channel`);
          }
        } else if (typeof url !== "string" || !url.startsWith("https://")) {
          problems.push(`channels[${i}].url must be an https URL`);
        } else if (seenUrls.has(url)) {
          problems.push(`channels[${i}].url repeats an earlier channel's URL`);
        } else {
          seenUrls.add(url);
        }
        if (typeof kind === "string" && CHANNEL_NAME.test(kind) && urlOk) {
          channels.push(isNpm ? { kind } : { kind, url: url as string });
        }
      }
    }
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
    integrity: integrity as string,
    channels,
  };
}
