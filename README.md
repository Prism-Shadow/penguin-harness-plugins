# PenguinHarness Plugin Index

The index of plugins available to [PenguinHarness][harness]. Plugin authors submit an
entry here; the harness reads the generated index and shows the result in its plugin
page.

**Published index:** <https://prism-shadow.github.io/penguin-plugins/index.json>

## What this repository is — and is not

It is a **catalogue**, not a package registry. Plugins are published to npm and
installed from there; an entry in this repository is metadata, a lock, a content hash,
and a promise that somebody checked it. Concretely:

- the code lives on **npm** — the harness resolves a plugin by its package specifier;
- the metadata lives **here** — one directory per entry under `packages/`;
- the artifact lives on **GitHub Pages** — `index.json`, rebuilt from the entries on
  every push to `main`.

Nothing in this repository executes a submission's code. The checks install packages
with scripts disabled and read them; they never import them.

## Layout

```
packages/<entry>/manifest.toml       one entry; the path is the bucket, the name,
packages/<entry>/package-lock.json   the version and the content's hash
plugin-index/                        the entry format as code, shared with the harness
src/                                 the checks and the index builder
docs/                                the submission rules in prose
```

`<entry>` is `[<@scope>/]<bucket>/<name>/<version>/<key>`. The bucket keeps every
directory narrow however many plugins are listed: it is read off the name without its
scope, the way the crates.io index files a crate — `1` or `2` for a name of that length,
`3/<first character>` for three characters, `<characters 1–2>/<characters 3–4>`
otherwise. `@penguinharness/sandbox-bwrap` 0.2.2 is under
`packages/@penguinharness/sa/nd/sandbox-bwrap/0.2.2/<key>/`.

`packages/` has the shape of a PenguinHarness machine's plugin store, whose entries add
the installed `package/` and a `.stored` marker beside the same two files. This
repository ignores those two, so a store is a checkout of this tree with the package
data filled in.

| Command                           | What it does                                                                       |
| --------------------------------- | ---------------------------------------------------------------------------------- |
| `pnpm new-entry <name>@<version>` | Writes a new entry for a published package: its lock, its integrity, its manifest. |
| `pnpm validate`                   | Schema, path and change-level rules (paths, deletions, ownership). Offline.        |
| `pnpm check-npm`                  | Installs changed entries from their locks and checks them. `--all` for everything. |
| `pnpm build`                      | Rebuilds `dist/index.json` from every entry.                                       |
| `pnpm test`                       | Unit tests for the checks and the shared package.                                  |

## Submitting a plugin

Read [CONTRIBUTING.md](CONTRIBUTING.md). The short version:

1. Publish your plugin to npm under any name (scoped is fine), with `"type": "module"`
   and a named `activate` export.
2. Run `pnpm new-entry <name>@<version>` and fill in `categories` and `owners` — see
   [docs/manifest.md](docs/manifest.md).
3. Open a pull request. CI checks the entry and the published package; a maintainer
   reviews the rest.

## Consuming the index

A deployment points its HTTP plugin registry at the published URL. The document is a
flat JSON array of entries — the same shape a machine's plugin store writes to its own
`index.json`, so a client that reads one reads the other.

```json
[
  {
    "name": "@acme/widgets",
    "version": "1.2.3",
    "description": "Widget tools for PenguinHarness.",
    "authors": ["Ada <@ada>"],
    "license": "MIT",
    "repository": "https://github.com/acme/widgets",
    "keywords": ["widgets", "linux"],
    "categories": ["tool"],
    "integrity": "sha256-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "updatedAt": 1755000000
  }
]
```

A name may appear more than once — several versions, or several contents of one version
— and the consumer picks by semver. Before it stores a package it fetched, it installs
the package the way the index did and compares the hash with `integrity`; a package
whose hash differs is refused.

Consumers should ignore fields they do not know: new optional fields are added without
a schema break, and an entry's name, version and integrity never change — a new
version or content is a new entry.

## License

The tooling in this repository is licensed under Apache-2.0. Listed plugins are
licensed by their authors, under the license each manifest declares.

[harness]: https://github.com/Prism-Shadow/penguin-harness
