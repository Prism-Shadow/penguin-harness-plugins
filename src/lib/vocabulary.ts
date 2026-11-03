/**
 * This repository's own policy, beside the manifest vocabulary the shared package checks
 * (`@prismshadow/penguin-plugin-index`): how large an entry may be, and who maintains the
 * index.
 */

/**
 * Entries above this are rejected: a plugin is code, not an asset bundle. Measured as the
 * archive the integrity is computed over — the package with every dependency it installs,
 * uncompressed — because that is what a machine stores and a push carries.
 */
export const MAX_ENTRY_BYTES = 64 * 1024 * 1024;

/**
 * GitHub handles that may merge changes to any manifest, including transferring a
 * plugin to new owners. Everyone else may only touch manifests they are listed in.
 */
export const INDEX_MAINTAINERS = ["Myriad-Dreamin"];
