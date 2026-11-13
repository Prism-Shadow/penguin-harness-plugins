---
name: penguin-plugin-index
description: Use for submitting a plugin to the PenguinHarness plugin index (penguin-harness-plugins) — at least one channel (npm or a GitHub release), the channel checks, owners/categories, one entry per pull request; new versions, yanking and ownership transfers later.
version: 0.1.1
---

# Submitting a plugin to the index

You are submitting a plugin to the PenguinHarness plugin index
(Prism-Shadow/penguin-harness-plugins). The protocol's authority is the
repository's [CONTRIBUTING.md](../../CONTRIBUTING.md); this skill mirrors it
step by step, and both are updated in the same PR when the protocol changes —
when the two disagree, CONTRIBUTING.md wins.

## Step 1 — One channel, reachable and hash-faithful

An entry needs **at least one channel** — a source the harness can fetch the
tarball from. Two shapes:

- **npm**: the package is published on npm. The tooling writes this channel for
  you (`pnpm new-entry` below).
- **GitHub release** (or any tarball URL): publish a release whose asset is the
  same bytes, and add a `[[channels]]` table per source with its `kind` and the
  tarball URL (see [docs/manifest.md](../../docs/manifest.md)). No
  `package-lock.json` needed on a channels-only entry.

Whatever the channel, CI fetches the tarball from the URL and checks the bytes
hash to the entry's `integrity` — so publish exactly the bytes the hash names.
An npm channel additionally pins `name`/`version` against what npm serves.

Entry-side facts (`"type": "module"`, a loadable entry, a license) are **CI's
job, not yours to restate**: the checks install the channel's bytes and verify
them. You fix what CI reports; you do not pre-argue it.

## Step 2 — One entry, written by the tooling

In a checkout of this repository (or a fork you will open the PR from):

```sh
pnpm install
pnpm new-entry <name>@<version>        # npm channel
```

That resolves the package's lock against npm, installs from it (scripts
disabled), computes the content hash, and writes
`packages/<bucket>/<name>/<version>/` with two files: `package-lock.json` and a
`manifest.toml` filled in from your `package.json`. **Never edit `integrity`** —
it is the hash the checks pin. A channels-only entry: write the `manifest.toml`
yourself following [docs/manifest.md](../../docs/manifest.md) (no
`package-lock.json`).

## Step 3 — The metadata you own

Fill in:

- `categories` — 1–3, from [docs/categories.md](../../docs/categories.md);
- `owners` — the GitHub handles allowed to change this entry later and to add
  further entries under this name, including yours. CI enforces it.

Everything else the tooling wrote; leave it.

## Step 4 — Local checks

```sh
pnpm validate && pnpm check-npm
```

Read the failures in place: the manifest parses and its path spells its name,
version and hash; each channel URL serves bytes that hash to `integrity`; an
npm channel's metadata matches what npm serves. Nothing in this repository
executes a submission's code.

## Step 5 — One pull request, one entry

Open the PR with exactly one entry and stop there. Review is human; CI decides
the mechanical questions.

## Later

- **New version**: publish it to your channel, add the new entry
  (`pnpm new-entry` on npm; a new `manifest.toml` for a channels-only entry),
  open another PR. The new entry sits beside the old ones; the harness picks
  among a name's entries by semver. Only the name's `owners` may add entries.
- **Moving is not possible** — an entry's path is its name, version and content.
- **Yanking** (`yanked = true` with a reason) and **transferring ownership**
  (editing `owners`) are in [CONTRIBUTING.md](../../CONTRIBUTING.md); a current
  owner opens that PR, or an issue when nobody can.
