# Submitting an extension

Thanks for building something for PenguinHarness. This page is the whole process; the
rules each check enforces are spelled out in [docs/manifest.md](docs/manifest.md),
[docs/categories.md](docs/categories.md) and [docs/licensing.md](docs/licensing.md).

## Before you open a pull request

Your extension must already be **published on npm**. This index points at npm; it does not
host code, and an entry for an unpublished package cannot pass CI.

The published package must:

- carry any valid npm name, optionally under your own scope (`@you/widgets`); there is
  no required prefix;
- declare `"type": "module"` — the harness imports extensions as ESM;
- export a named `activate` function from its entry point. That is the extension contract:
  `activate(ctx)` runs once per process at load, and every harness view arrives through
  `ctx.on(...)` subscriptions made during that call;
- carry an [allowed license](docs/licensing.md), both in `package.json` and as a license
  file in the repository;
- have a public source repository. An extension nobody can read is an extension nobody can
  review.

Test it locally first: add the package to your own deployment's `extensions.json` and
confirm the harness loads it and the capability it registers works.

## The pull request

Add exactly one file: `extensions/<npm package name>.toml`. The path spells the package
name, so `@acme/widgets` lives at
`extensions/@acme/widgets.toml`.

```toml
name = "@acme/widgets"
version = "1.2.3"
description = "Widget tools for PenguinHarness."
authors = ["Ada Lovelace <@ada>"]
license = "MIT"
repository = "https://github.com/acme/widgets"
homepage = "https://example.com/widgets"      # optional
keywords = ["widgets", "linux"]               # optional, at most 5
categories = ["tool"]
owners = ["ada"]
```

`owners` lists the GitHub handles allowed to change this entry later, including yours.
CI enforces it, so keep it accurate.

Then run the checks locally if you like — `pnpm install && pnpm validate && pnpm check-npm`
— and open the PR. One extension per pull request.

## What review looks for

CI decides the mechanical questions: the manifest parses, the metadata matches what npm
serves, the package is loadable ESM with an `activate` export, the license is allowed,
the version did not go backwards. A maintainer then looks at the rest:

- **Usefulness.** The extension should do something other people want, and do it through
  the harness's own capability vocabulary rather than by working around it.
- **Naming.** Names are first-come, but a name that claims a whole problem space
  (`sandbox`) will be asked to become specific (`sandbox-bwrap`). Names are permanent — see below.
- **Scope of what it takes.** An extension that spawns processes, reads outside the project
  root, or talks to the network should say so plainly in its README.
- **Safety.** Extensions must not attempt to exfiltrate user data, disable the sandbox
  behind the operator's back, or obfuscate what they run.

Meeting the mechanical checks does not guarantee listing. If we ask for changes, it is
to get the entry merged, not to keep it out.

## Updating an entry

Publish the new version to npm, then bump `version` in your manifest and open a PR.
Versions may not decrease, and only listed `owners` may change the file.

## Renaming, yanking and transferring

- **Renaming is not possible.** The manifest path is the package name; changing it would
  repoint an entry people already trust. Publish under the new name and yank the old
  entry.
- **Yanking** takes an extension out of the catalogue without erasing the record:

  ```toml
  yanked = true
  yanked_reason = "Superseded by @acme/widgets-2."
  ```

  The entry disappears from `index.json`; already-installed deployments are unaffected,
  because npm still serves the package.

- **Transferring** ownership means editing `owners`. A current owner opens that PR; if
  nobody can, open an issue and a maintainer will handle it.

## Reporting a problem with a listed extension

Bugs go to the extension's own repository. Open an issue **here** only for the index
itself, or for a listed extension that is malicious, misrepresents what it does, or has
gone unmaintained in a way that puts users at risk.
