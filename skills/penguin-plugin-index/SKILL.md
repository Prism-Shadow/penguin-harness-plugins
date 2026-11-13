---
name: penguin-plugin-index
description: Use for submitting a plugin to the PenguinHarness plugin index (penguin-harness-plugins) — prerequisites, `pnpm new-entry`, owners/categories, local checks, one entry per pull request; new versions, yanking and ownership transfers later.
version: 0.1.0
---

# Submitting a plugin to the index

You are submitting a plugin to the PenguinHarness plugin index
(Prism-Shadow/penguin-harness-plugins). The protocol's authority is the
repository's [CONTRIBUTING.md](../../CONTRIBUTING.md); this skill mirrors it
step by step, and both are updated in the same PR when the protocol changes —
when the two disagree, CONTRIBUTING.md wins.

## Step 1 — Prerequisites, before anything touches the index

Check every one, and stop at the first failure with the fix in hand:

- the package is **published on npm** (any valid name, scoped to you is fine);
- `package.json` declares `"type": "module"` — the harness imports plugins as ESM;
- the entry point exports a **named `activate`** function (`activate(ctx)` runs
  once per process at load; every harness view arrives through `ctx.on(...)`
  subscriptions made during that call);
- an **allowed license**, both in `package.json` and as a license file in the
  repository (see docs/licensing.md);
- a **public source repository**.

Test it first: name the package in your own deployment's Project `[plugins]`
table and confirm the harness loads it and the capability it registers works.

## Step 2 — One entry, written by the tooling

In a checkout of this repository (or a fork you will open the PR from):

```sh
pnpm install
pnpm new-entry <name>@<version>
```

That resolves the package's lock against npm, installs from it (scripts
disabled), computes the content hash, and writes
`packages/<bucket>/<name>/<version>/` with two files: `package-lock.json` and a
`manifest.toml` filled in from your `package.json`. **Never edit `integrity`** —
it is the hash the checks pin.

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

Read the failures in place. The checks decide the mechanical questions: the
manifest parses and its path spells its name, version and hash; the metadata
matches what npm serves; installing the lock produces exactly the content the
hash names; the package is loadable ESM with an `activate` export; the license
is allowed. Nothing in this repository executes a submission's code.

## Step 5 — One pull request, one entry

Open the PR with exactly one entry and stop there. Review is human; CI decides
the mechanical questions. If your plugin is reachable beyond npm — a GitHub
release, a mirror — add a `[[channels]]` table per source (see
[docs/manifest.md](../../docs/manifest.md)): CI fetches each URL and checks the
tarball hashes to `integrity`, so publish the same bytes `npm pack` produces.

## Later

- **New version**: publish to npm, run `pnpm new-entry <name>@<new version>`,
  open another PR. The new entry sits beside the old ones; the harness picks
  among a name's entries by semver. Only the name's `owners` may add entries.
- **Moving is not possible** — an entry's path is its name, version and content.
- **Yanking** (`yanked = true` with a reason) and **transferring ownership**
  (editing `owners`) are in [CONTRIBUTING.md](../../CONTRIBUTING.md); a current
  owner opens that PR, or an issue when nobody can.
