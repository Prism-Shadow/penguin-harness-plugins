---
name: penguin-plugin-index
description: Use for submitting a plugin to the PenguinHarness plugin index (penguin-harness-plugins) — one channel (npm or a release), one tooling-written entry per pull request; new versions, yanking and ownership transfers later.
version: 0.2.0
---

# Submitting a plugin to the index

You are submitting a plugin to the PenguinHarness plugin index
(Prism-Shadow/penguin-harness-plugins). [CONTRIBUTING.md](../../CONTRIBUTING.md)
is the authority; this skill drives the steps and does not restate the rules —
when the two disagree, CONTRIBUTING.md wins.

1. **Check one channel exists.** The entry needs at least one source the harness
   can fetch the tarball from: npm, or a `[[channels]]` table pointing at a
   GitHub release (or any tarball URL), per
   [docs/manifest.md](../../docs/manifest.md). CI fetches each URL and checks
   the bytes hash to the entry's `integrity`; publish exactly those bytes.
   Entry-side facts (ESM, a loadable entry, a license) are CI's to check — you
   fix what CI reports.
2. **Let the tooling write the entry.** In a checkout of this repository, run
   `pnpm install && pnpm new-entry <name>@<version>` for an npm channel. For a
   channels-only entry, write the `manifest.toml` yourself following
   [docs/manifest.md](../../docs/manifest.md) (no `package-lock.json`). Never
   edit `integrity`.
3. **Fill in `categories`** (1–3, from
   [docs/categories.md](../../docs/categories.md)) **and `owners`** — the
   GitHub handles allowed to change this entry later, including yours.
   Everything else the tooling wrote; leave it.
4. **Run `pnpm validate && pnpm check-npm`** and fix what they report. Nothing
   here executes a submission's code.
5. **Open one pull request with exactly one entry** and stop there — review is
   human, CI decides the mechanical questions.

Later: a new version is a new entry beside the old ones (`pnpm new-entry` or a
new `manifest.toml`); moving is not possible — the path is its name, version
and content; yanking and ownership transfers are in
[CONTRIBUTING.md](../../CONTRIBUTING.md).
