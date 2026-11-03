# Submitting a plugin

Thanks for building something for PenguinHarness. This page is the whole process; the
rules each check enforces are spelled out in [docs/manifest.md](docs/manifest.md),
[docs/categories.md](docs/categories.md) and [docs/licensing.md](docs/licensing.md).

## Before you open a pull request

Your plugin must already be **published on npm**. This index points at npm; it does not
host code, and an entry for an unpublished package cannot pass CI.

The published package must:

- carry any valid npm name, optionally under your own scope (`@you/widgets`); there is
  no required prefix;
- declare `"type": "module"` — the harness imports plugins as ESM;
- export a named `activate` function from its entry point. That is the plugin contract:
  `activate(ctx)` runs once per process at load, and every harness view arrives through
  `ctx.on(...)` subscriptions made during that call;
- carry an [allowed license](docs/licensing.md), both in `package.json` and as a license
  file in the repository;
- have a public source repository. A plugin nobody can read is a plugin nobody can
  review.

Test it locally first: name the package in your own deployment's Project `[plugins]`
table and confirm the harness loads it and the capability it registers works.

## The pull request

Add exactly one entry. Let the tooling write it:

```sh
pnpm install
pnpm new-entry @acme/widgets@1.2.3
```

That resolves the package's lock against npm, installs from it (scripts disabled),
computes the content hash, and writes
`plugins/@acme/widgets/1.2.3/<first 16 hex digits of the hash>/` with two files:
`package-lock.json`, and a `manifest.toml` filled in from your `package.json`:

```toml
name = "@acme/widgets"
version = "1.2.3"
description = "Widget tools for PenguinHarness."
authors = ["Ada Lovelace <@ada>"]
license = "MIT"
repository = "https://github.com/acme/widgets"
homepage = "https://example.com/widgets"      # optional
keywords = ["widgets", "linux"]               # optional, at most 5
categories = []                               # fill in: 1–3 categories
owners = []                                   # fill in: your GitHub handle
integrity = "sha256-…"                        # written by new-entry; do not edit
```

Fill in `categories` and `owners`. `owners` lists the GitHub handles allowed to change
this entry later and to add further entries under this name, including yours. CI
enforces it, so keep it accurate.

Then run the checks locally if you like — `pnpm validate && pnpm check-npm` — and open
the PR. One entry per pull request.

## What review looks for

CI decides the mechanical questions: the manifest parses and its path spells its name,
version and hash; the metadata matches what npm serves; installing the lock produces
exactly the content the hash names; the package is loadable ESM with an `activate`
export; the license is allowed. A maintainer then looks at the rest:

- **Usefulness.** The plugin should do something other people want, and do it through
  the harness's own capability vocabulary rather than by working around it.
- **Naming.** Names are first-come, but a name that claims a whole problem space
  (`sandbox`) will be asked to become specific (`sandbox-bwrap`). Paths are permanent — see below.
- **Scope of what it takes.** A plugin that spawns processes, reads outside the project
  root, or talks to the network should say so plainly in its README.
- **Safety.** Plugins must not attempt to exfiltrate user data, disable the sandbox
  behind the operator's back, or obfuscate what they run.

Meeting the mechanical checks does not guarantee listing. If we ask for changes, it is
to get the entry merged, not to keep it out.

## A new version

Publish it to npm, then run `pnpm new-entry <name>@<new version>` and open a PR. The new
entry sits beside the old ones; nothing is edited, and the harness picks among a name's
entries by semver. Only the name's `owners` may add entries under it.

## Moving, yanking and transferring

- **Moving is not possible.** An entry's path is its name, its version and its content;
  changing it would repoint an entry people already trust. Add the new entry and yank the
  old one.
- **Yanking** takes an entry out of the catalogue without erasing the record:

  ```toml
  yanked = true
  yanked_reason = "Superseded by @acme/widgets-2."
  ```

  The entry disappears from `index.json`; deployments that already stored it are
  unaffected, because npm still serves the package.

- **Transferring** ownership means editing `owners`. A current owner opens that PR; if
  nobody can, open an issue and a maintainer will handle it.

## Reporting a problem with a listed plugin

Bugs go to the plugin's own repository. Open an issue **here** only for the index
itself, or for a listed plugin that is malicious, misrepresents what it does, or has
gone unmaintained in a way that puts users at risk.
