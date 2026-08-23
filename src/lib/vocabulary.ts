/**
 * The closed vocabularies a submission is checked against.
 *
 * Every list here is deliberately short and deliberately closed: an open set of
 * categories degenerates into free-form tags that the Web App cannot group, and an
 * open set of licenses means the index recommends software nobody vetted. Widening a
 * list is a normal PR against this file — inventing a value in a plugin manifest is
 * not.
 */

/**
 * Capability floors a plugin can provide on. These mirror the harness's own capability
 * stack (terminal → sandbox → tool → workflow); `integration` and `utility` are the
 * escape hatches for plugins that sit beside the stack rather than on one of its floors.
 */
export const CATEGORIES = [
  "sandbox",
  "tool",
  "workflow",
  "terminal",
  "integration",
  "utility",
] as const;

/** A plugin declares at least one and at most this many categories. */
export const MAX_CATEGORIES = 3;

/** Keywords are free-form, but bounded so the chip row in the Web App stays readable. */
export const MAX_KEYWORDS = 5;
export const MAX_KEYWORD_LENGTH = 20;

/** Matches crates.io's cap; a description is a one-liner, not a README. */
export const MAX_DESCRIPTION_LENGTH = 1000;

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

/**
 * Every plugin package's unscoped name starts with this. The harness resolves plugins
 * by npm specifier, so the prefix is what makes a package findable as a PenguinHarness
 * plugin on npm itself, where our index has no reach.
 */
export const NAME_PREFIX = "penguin-plugin-";

/** Tarballs above this are rejected: a plugin is code, not an asset bundle. */
export const MAX_TARBALL_BYTES = 10 * 1024 * 1024;

/**
 * GitHub handles that may merge changes to any manifest, including transferring a
 * plugin to new owners. Everyone else may only touch manifests they are listed in.
 */
export const INDEX_MAINTAINERS = ["Myriad-Dreamin"];
