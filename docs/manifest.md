# An entry and its manifest

An entry is one package at one version with one content. It is a directory:

```
packages/[<@scope>/]<bucket>/<name>/<version>/<first 16 hex digits of integrity>/
  manifest.toml
  package-lock.json
```

The bucket is read off the name without its scope, lower-cased: `1` or `2` for a name of
that length, `3/<first character>` for three characters, and `<characters 1–2>/<characters
3–4>` otherwise (the crates.io index's rule). It keeps every directory narrow however
many plugins are listed.

The path is not a convention the tooling tolerates — it is checked four ways. It
spells the package's `name` (`@acme/widgets` lives under `packages/@acme/wi/dg/widgets/`
and nowhere else), the bucket that name files under, its `version`, and the first 16 hex
digits of its `integrity`. So a new
version is a new directory, a new content of one version is another directory beside
it, and nothing about a listed entry can change without showing up as a move.

The tree has the shape of a PenguinHarness machine's plugin store. A store keeps the
same two files and adds `package/` (the installed package) and `.stored` beside them;
this repository ignores those two, so it is a store without package data and a store
is a checkout of it with the data filled in.

Unknown keys are rejected. A typo in a key name is a mistake we would rather report than
silently ignore. Nothing else may live under `packages/`.

## Fields

| Key             | Required      | Rule                                                                                                                                                               |
| --------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name`          | yes           | The npm specifier, matching the path. Any valid npm package name, scoped or not.                                                                                   |
| `version`       | yes           | An exact semantic version — never a range — matching the path. Must be published on npm.                                                                           |
| `integrity`     | yes           | `sha256-` and 64 lowercase hex digits: the hash of the package as installed from the lock (see below). Its first 16 digits are the entry's directory.              |
| `description`   | yes           | One line, at most 1000 characters, identical to the published package's `description`.                                                                             |
| `authors`       | yes           | Non-empty. See the format below.                                                                                                                                   |
| `license`       | yes           | An [allowed](licensing.md) SPDX expression, identical to the published package's `license`.                                                                        |
| `repository`    | yes           | `https://` URL of the source repository. Must agree with the published package's `repository`.                                                                     |
| `homepage`      | no            | `https://` URL.                                                                                                                                                    |
| `keywords`      | no            | At most 5, lowercase, alphanumeric with hyphens, at most 20 characters each. Use them for the target platforms (`linux`, `macos`, `windows`) and for search terms. |
| `categories`    | yes           | 1–3 values from the [category list](categories.md).                                                                                                                |
| `owners`        | yes           | GitHub handles allowed to change this entry and to add entries under its name.                                                                                     |
| `yanked`        | no            | `true` removes the entry from the published index. Requires `yanked_reason`.                                                                                       |
| `yanked_reason` | with `yanked` | Why the entry was pulled.                                                                                                                                          |

## The lock

`package-lock.json` is npm's lock (lockfile version 2 or 3) of a prefix whose only
dependency is the package, at exactly the entry's version. It is what makes the entry
reproducible: installing it yields the same tree on every machine, each tarball checked
against the lock's own hashes. `pnpm new-entry` writes it; you do not.

## The integrity

The package is installed from the lock with `npm ci --ignore-scripts --omit=dev --force`
— `--force` so that every platform's optional packages the lock names are installed,
whatever machine runs it. Its directory, with the dependencies npm hoisted beside it
folded into its own `node_modules`, is archived by a deterministic archiver (sorted
entries, epoch mtimes, no owners, 0755 for what executes and 0644 otherwise, no
compression), and `integrity` is the sha256 of that archive.

The install, the archiver and the hash live in one small package,
`@prismshadow/penguin-plugin-index`, which this repository's checks and the harness's
plugin store both import. The harness makes the same computation before it stores a
package fetched from npm, and refuses one whose value is not the index's.

## Author format

An author is a display name, optionally followed by exactly one contact in angle
brackets — a GitHub handle, an `https://` URL, or an email address:

```toml
authors = [
  "Ada Lovelace",
  "Ada Lovelace <@ada>",
  "Ada Lovelace <https://example.com/ada>",
  "Ada Lovelace <ada@example.com>",
]
```

The contact is what lets a reviewer tell whether the person submitting an entry is the
person it credits.

## Naming rules

There is no required prefix: the entry's path already identifies the package, and npm
is where the name has to be unique. Prefer a name that says what your plugin _is_
rather than what the whole category _is_. `sandbox-bwrap` names an implementation;
`sandbox` claims the floor. First-come applies, but a submission holding a
category-wide name will be asked to narrow it.

Paths are permanent. There is no rename path — only adding a new entry and yanking the
old one.

## What is checked, and where

**Offline** (`pnpm validate`): every rule in the table above, the lock's shape, and
that nothing but entries lives under `packages/`; plus the change-level rules — no moved
entries, no deletions in place of a yank, only a name's owners may add entries under it,
and only an entry's owners may edit it.

**Against npm** (`pnpm check-npm`): the version exists and is not deprecated; license,
description and repository match the published package; installing the lock produces
exactly the content `integrity` names, archived to at most 64 MiB; and the package is
ESM whose entry point declares an `activate` export.

That last check is **static**: the install runs no scripts and the entry file is read,
never imported. Executing submitted code in CI would hand every contributor a shell on
the machine that publishes this index. If your plugin is bundled in a way that hides the
export from a source-level read, say so in the pull request.
