# The extension manifest

One TOML file per extension, at `extensions/<npm package name>.toml`. The path is not a
convention the tooling tolerates — it is checked: `@acme/penguin-extension-widgets` must
live at `extensions/@acme/penguin-extension-widgets.toml` and nowhere else. That is what makes
a rename visible as a file move and makes two entries claiming one package impossible.

Unknown keys are rejected. A typo in a key name is a mistake we would rather report than
silently ignore.

## Fields

| Key             | Required      | Rule                                                                                                                                                               |
| --------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `name`          | yes           | The npm specifier, matching the file path. The part after the scope must start with `penguin-extension-`.                                                          |
| `version`       | yes           | An exact semantic version — never a range. Must be published on npm, and must not decrease across updates.                                                         |
| `description`   | yes           | One line, at most 1000 characters, identical to the published package's `description`.                                                                             |
| `authors`       | yes           | Non-empty. See the format below.                                                                                                                                   |
| `license`       | yes           | An [allowed](licensing.md) SPDX expression, identical to the published package's `license`.                                                                        |
| `repository`    | yes           | `https://` URL of the source repository. Must agree with the published package's `repository`.                                                                     |
| `homepage`      | no            | `https://` URL.                                                                                                                                                    |
| `keywords`      | no            | At most 5, lowercase, alphanumeric with hyphens, at most 20 characters each. Use them for the target platforms (`linux`, `macos`, `windows`) and for search terms. |
| `categories`    | yes           | 1–3 values from the [category list](categories.md).                                                                                                                |
| `owners`        | yes           | GitHub handles allowed to change this manifest.                                                                                                                    |
| `yanked`        | no            | `true` removes the entry from the published index. Requires `yanked_reason`.                                                                                       |
| `yanked_reason` | with `yanked` | Why the extension was pulled.                                                                                                                                      |

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

The `penguin-extension-` prefix is required because npm is where people will actually find
your package, and our index has no reach there.

Beyond the prefix, prefer a name that says what your extension _is_ rather than what the
whole category _is_. `penguin-extension-sandbox-bwrap` names an implementation;
`penguin-extension-sandbox` claims the floor. First-come applies, but a submission holding a
category-wide name will be asked to narrow it.

Names are permanent. There is no rename path — only publishing under a new name and
yanking the old entry.

## What is checked, and where

**Offline** (`pnpm validate`): every rule in the table above, plus the change-level
rules — no renames, no version decreases, no deletions in place of a yank, and only
listed owners may edit an entry.

**Against npm** (`pnpm check-npm`): the version exists and is not deprecated; license,
description and repository match the published package; the tarball matches its
integrity hash, is under 10 MiB, is ESM, and its entry point declares an `activate`
export.

That last check is **static**: the tarball is unpacked and read, never imported.
Executing submitted code in CI would hand every contributor a shell on the machine that
publishes this index. If your extension is bundled in a way that hides the export from a
source-level read, say so in the pull request.
