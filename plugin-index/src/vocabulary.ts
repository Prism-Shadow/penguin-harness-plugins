/**
 * The closed vocabularies a manifest is checked against.
 *
 * Every list here is deliberately short and deliberately closed: an open set of
 * categories degenerates into free-form tags that the Web App cannot group, and an
 * open set of licenses means the index recommends software nobody vetted. Widening a
 * list is a normal PR against this file — inventing a value in a plugin manifest is
 * not.
 */

/**
 * The plugin page groups by audience, and this list mirrors the harness's own
 * `PLUGIN_CATEGORIES` one for one — same ids, same order, same intent. The two lists
 * must stay in sync: a category the index accepts but the page does not know lands the
 * plugin in Other, and one the page knows but the index rejects is unlistable. Widening
 * either is a pull request against both files at once.
 */
export const CATEGORIES = [
  "rsi",
  "office-productivity",
  "software-development",
  "ai-app-development",
  "sandbox",
] as const;

/** A plugin declares at least one and at most this many categories. */
export const MAX_CATEGORIES = 3;

/** Keywords are free-form, but bounded so the chip row in the Web App stays readable. */
export const MAX_KEYWORDS = 5;
export const MAX_KEYWORD_LENGTH = 20;

/** Matches crates.io's cap; a description is a one-liner, not a README. */
export const MAX_DESCRIPTION_LENGTH = 1000;

/**
 * Distribution channels a manifest declares — at least one, at most this many. Channels
 * are peers: `kind = "npm"` is the registry, anything else a source beyond it (a GitHub
 * release, a vendor's mirror), each pinned by the hash of the tarball it serves.
 */
export const MAX_CHANNELS = 8;

/** A channel's kind: lowercase alphanumeric with hyphens, at most 20 characters. */
export const CHANNEL_NAME = /^[a-z0-9][a-z0-9-]{0,19}$/;

/**
 * SPDX identifiers a listed plugin may use. OSI-approved permissive and copyleft
 * licenses plus CC0 — the same shape as typst's rule (OSI-approved or an allowed CC
 * license), spelled out as an explicit list because we validate without an SPDX
 * database.
 */
export const LICENSES = [
  "MIT",
  "Apache-2.0",
  "BSD-2-Clause",
  "BSD-3-Clause",
  "ISC",
  "MPL-2.0",
  "LGPL-3.0-only",
  "LGPL-3.0-or-later",
  "GPL-3.0-only",
  "GPL-3.0-or-later",
  "AGPL-3.0-only",
  "AGPL-3.0-or-later",
  "BlueOak-1.0.0",
  "Unlicense",
  "CC0-1.0",
] as const;
