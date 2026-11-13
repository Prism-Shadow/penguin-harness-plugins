# Submitting with the plugin-submission skill

This page is the authoring-skill edition of [CONTRIBUTING.md](../CONTRIBUTING.md):
the skill walks the same protocol step by step, and a harness session can run it
with you. Nothing here changes the rules — when this page and CONTRIBUTING.md
disagree, CONTRIBUTING.md wins, and the skill is updated in the same PR.

## What the skill is

`SKILL.md` for a session: it carries the submission protocol as executable
steps — prerequisites, `pnpm new-entry`, the metadata you fill in, the local
checks, and the pull-request shape. It lives in this repository, at
`skills/penguin-plugin-index/SKILL.md`, so the skill ships with the very
repository whose protocol it teaches.

## What the skill does, step by step

1. **Prerequisites** — it checks before touching the index: the package is
   published on npm, `"type": "module"`, a named `activate` export, an allowed
   license in `package.json` and as a license file, a public source repository.
2. **One entry** — it runs `pnpm new-entry <name>@<version>` in a checkout of
   this repository. That resolves the lock against npm, installs with scripts
   disabled, computes the content hash, and writes
   `packages/<bucket>/<name>/<version>/` with `package-lock.json` and a
   `manifest.toml` filled from the package.
3. **Metadata you own** — it asks you for `categories` (1–3, from
   [docs/categories.md](categories.md)) and `owners` (GitHub handles allowed to
   change the entry later); it never edits `integrity`.
4. **Local checks** — it runs `pnpm validate && pnpm check-npm` and reads you
   the failures in place.
5. **The pull request** — it opens one PR with exactly one entry, and stops
   there: review is human, and CI decides the mechanical questions.

## Beyond npm

`pnpm new-entry` writes the npm channel. If your plugin is also reachable from
a GitHub release or a mirror, the skill adds a `[[channels]]` table per source
and CI fetches each URL to check the bytes hash to `integrity` — see
[docs/manifest.md](manifest.md).

## New versions, yanking, ownership

The skill re-runs `pnpm new-entry` for a new version and never edits an old
entry. Yanking and transferring ownership stay maintainer-side actions — the
skill points at the [CONTRIBUTING.md](../CONTRIBUTING.md) section instead of
doing them.
