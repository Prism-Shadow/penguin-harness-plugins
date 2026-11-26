/**
 * The PenguinHarness readers the published index has to satisfy (`pnpm check-harness`).
 *
 * A harness reads `index.json` with one strict reader, and a row that reader does not accept
 * makes the whole artifact broken to it: it refuses the entire document, and every remote plugin
 * disappears from that deployment's market — not just the row. So the gate parses the built index
 * with each reader exactly as its release shipped it, in CI and before a publish replaces the
 * live document.
 *
 * The readers are every version of READER_PACKAGE published on npm from READER_FLOOR up, found on
 * the registry at each run — a new harness release is checked from the moment it is published,
 * with nothing to add here — plus the harness's `main` branch, which the workflows check out and
 * pass as HARNESS_MAIN_READER.
 *
 * Why the floor is 0.2.19, and what raising it means:
 *
 * - 0.2.19 is the first release that ships the reader as `@prismshadow/penguin-core/plugin-index`,
 *   and the first that looks at an entry's `integrity` and `channels`.
 * - 0.2.15 to 0.2.18 read the index with a copy inside the server package, which cannot be
 *   imported without the whole server. Every check those copies make, 0.2.19's reader makes the
 *   same way (0.2.15 checks two fields fewer), so a document 0.2.19 accepts, they accept.
 * - Releases before 0.2.15 read no remote index at all.
 *
 * So this floor covers every release that reads the index. Raise it only once the releases below
 * the new floor are no longer supported: from then on, nothing vouches for 0.2.15 to 0.2.18 either.
 */

/** The npm package a harness release ships its index reader in. */
export const READER_PACKAGE = "@prismshadow/penguin-core";

/** The package export the reader is imported through, as the harness's server imports it. */
export const READER_EXPORT = "./plugin-index";

/** The oldest release whose reader the index must satisfy. */
export const READER_FLOOR = "0.2.19";
