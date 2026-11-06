# An entry and its manifest

An entry is one package at one version with one content. It is a directory:

```
packages/[<@scope>/]<bucket>/<name>/<version>/
  manifest.toml
  package-lock.json
```

— or `packages/[<@scope>/]<bucket>/<name>/<version>/<key>/` once more than one content
shares the name and version. The keyless form is the ordinary one: a version has one
content, and validation refuses a keyless entry beside any other entry of the same name
and version. The keyed form exists for a store holding an older content beside a fresh
one while it hot-updates; the key is the `integrity`'s digest in base64url.

The bucket is read off the name without its scope, lower-cased: `1` or `2` for a name of
that length, `3/<first character>` for three characters, and `<characters 1–2>/<characters
3–4>` otherwise (the crates.io index's rule). It keeps every directory narrow however
many plugins are listed.

The path is not a convention the tooling tolerates — it is checked. It spells the
package's `name` (`@acme/widgets` lives under `packages/@acme/wi/dg/widgets/` and
nowhere else), the bucket that name files under, and its `version` — plus, on a keyed
path, the base64url of the `integrity`'s digest. So a new version is a new directory, a
new content of one version is another directory beside it, and nothing about a listed
entry can change without showing up as a move.

The tree has the shape of a PenguinHarness machine's plugin store. A store keeps the
same two files and adds `package/` (the installed package) and `.stored` beside them;
this repository ignores those two, so it is a store without package data and a store
is a checkout of it with the data filled in.

Unknown keys are rejected. A typo in a key name is a mistake we would rather report than
silently ignore. Nothing else may live under `packages/`.

## Fields

| Key             | Required      | Rule                                                                                                                                                                                                                                                       |
| --------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`          | yes           | The npm specifier, matching the path. Any valid npm package name, scoped or not.                                                                                                                                                                           |
| `version`       | yes           | An exact semantic version — never a range — matching the path. Must be published on npm.                                                                                                                                                                   |
| `integrity`     | yes           | npm's content hash: `sha512-` followed by 86 base64 characters — the same value the registry reports as `dist.integrity` (see below). Its digest in base64url is the entry directory's key, present only when several contents share the name and version. |
| `description`   | yes           | One line, at most 1000 characters, identical to the published package's `description`.                                                                                                                                                                     |
| `authors`       | yes           | Non-empty. See the format below.                                                                                                                                                                                                                           |
| `license`       | yes           | An [allowed](licensing.md) SPDX expression, identical to the published package's `license`.                                                                                                                                                                |
| `repository`    | yes           | `https://` URL of the source repository. Must agree with the published package's `repository`.                                                                                                                                                             |
| `homepage`      | no            | `https://` URL.                                                                                                                                                                                                                                            |
| `title`         | no            | The plugin's card name, as the package's own manifest declares it — what an uninstalled plugin's card shows instead of the package name. A non-empty string.                                                                                               |
| `title_zh`      | no            | The card name in Chinese, beside `title`. A non-empty string.                                                                                                                                                                                              |
| `keywords`      | no            | At most 5, lowercase, alphanumeric with hyphens, at most 20 characters each. Use them for the target platforms (`linux`, `macos`, `windows`) and for search terms.                                                                                         |
| `categories`    | yes           | 1–3 values from the [category list](categories.md).                                                                                                                                                                                                        |
| `owners`        | yes           | GitHub handles allowed to change this entry and to add entries under its name.                                                                                                                                                                             |
| `channels`      | yes           | Where the package can be installed from, npm being one possible kind — see [Channels](#channels) below.                                                                                                                                                    |
| `yanked`        | no            | `true` removes the entry from the published index. Requires `yanked_reason`.                                                                                                                                                                               |
| `yanked_reason` | with `yanked` | Why the entry was pulled.                                                                                                                                                                                                                                  |

## Channels

An entry lists where it can be installed from as one or more channels — at least one,
at most 8. Channels are peers, and `kind` says which: `npm` is the registry, anything
else a tarball served from somewhere else (a GitHub release, a vendor's mirror). Every
channel must serve byte-identical tarballs — the ones the entry's `integrity` names:

```toml
integrity = "sha512-…" # the canonical hash: every channel serves these bytes

[[channels]]
kind = "npm"

[[channels]]
kind = "github"
url = "https://github.com/acme/widgets/releases/download/v1.2.3/widgets-1.2.3.tgz"
```

| Key    | Required            | Rule                                                                               |
| ------ | ------------------- | ---------------------------------------------------------------------------------- |
| `kind` | yes                 | Lowercase alphanumeric with hyphens, at most 20 characters. `npm` is the registry. |
| `url`  | unless `kind="npm"` | `https://` URL of the tarball as the channel serves it. Must be absent for `npm`.  |

Kinds and URLs must not repeat, and there is at most one `npm` channel. An entry with an
`npm` channel carries a `package-lock.json` and is checked against the registry —
metadata agreement, then install-from-lock with a size check — while each URL channel is
checked by fetching its tarball, hashing it against `integrity`, installing it (scripts
disabled) and running the same static probe on it. An entry need not have an `npm`
channel at all; it must have some channel, because an installable-from-nowhere entry is
not listable.

## The lock

`package-lock.json` is npm's lock (lockfile version 2 or 3) of a prefix whose only
dependency is the package, at exactly the entry's version. It is what makes the entry
reproducible: installing it yields the same tree on every machine, each tarball checked
against the lock's own hashes. `pnpm new-entry` writes it; you do not.

## The integrity

`integrity` is npm's own content hash, copied from the registry: `sha512-` followed by
86 base64 characters, the value the registry reports as `dist.integrity` for that
version. It pins the package's tarball bytes, and every channel must serve a tarball
that hashes to exactly it — for a GitHub release channel that means publishing the same
bytes `npm pack` produces. An entry's directory carries that digest in base64url
(`+` as `-`, `/` as `_`, no padding) as its key — but only when more than one content
shares the name and version; the ordinary entry has no key segment at all.

The install, the hash and the checks live in one small package,
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
entries (a maintainer may carry a schema migration that moves them), no deletions in
place of a yank, only a name's owners may add entries under it, and only an entry's
owners may edit it.

**Against npm** (`pnpm check-npm`): the version exists and is not deprecated; license,
description and repository match the published package; installing the lock yields a
package of at most 64 MiB; and the package is ESM whose entry point declares an
`activate` export.

That last check is **static**: the install runs no scripts and the entry file is read,
never imported. Executing submitted code in CI would hand every contributor a shell on
the machine that publishes this index. If your plugin is bundled in a way that hides the
export from a source-level read, say so in the pull request.
