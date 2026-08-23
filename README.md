# PenguinHarness Plugin Index

The index of plugins available to [PenguinHarness][harness]. Plugin authors submit an
entry here; the harness reads the generated index and shows the result in its plugin
page.

**Published index:** <https://prism-shadow.github.io/penguin-plugins/index.json>

## What this repository is — and is not

It is a **catalogue**, not a package registry. Plugins are published to npm and
installed from there; an entry in this repository is metadata plus a promise that
somebody checked it. Concretely:

- the code lives on **npm** — the harness resolves a plugin by its package specifier;
- the metadata lives **here** — one TOML manifest per plugin under `plugins/`;
- the artifact lives on **GitHub Pages** — `index.json`, rebuilt from the manifests on
  every push to `main`.

Nothing in this repository executes a submission's code. The checks read published
tarballs; they never import them.

## Layout

```
plugins/@scope/penguin-plugin-name.toml   one manifest per plugin; the path is the npm name
src/                                      the checks and the index builder
docs/                                     the submission rules in prose
```

| Command          | What it does                                                                 |
| ---------------- | ---------------------------------------------------------------------------- |
| `pnpm validate`  | Schema and change-level rules (versions, renames, ownership). Offline.       |
| `pnpm check-npm` | Cross-checks changed manifests against npm. Add `--all` to sweep everything. |
| `pnpm build`     | Rebuilds `dist/index.json` from every manifest.                              |
| `pnpm test`      | Unit tests for the checks themselves.                                        |

## Submitting a plugin

Read [CONTRIBUTING.md](CONTRIBUTING.md). The short version:

1. Publish your plugin to npm as `penguin-plugin-<something>` (scoped is fine), with
   `"type": "module"` and a named `activate` export.
2. Add `plugins/<npm name>.toml` — see [docs/manifest.md](docs/manifest.md).
3. Open a pull request. CI checks the manifest and the published package; a maintainer
   reviews the rest.

## Consuming the index

A deployment points its HTTP plugin registry at the published URL. The document is a
flat JSON array of entries matching the harness's `PluginIndexEntry` — the same schema
the harness's builtin index uses, so a client that reads one reads the other.

```json
[
  {
    "name": "@acme/penguin-plugin-widgets",
    "version": "1.2.3",
    "description": "Widget tools for PenguinHarness.",
    "authors": ["Ada <@ada>"],
    "license": "MIT",
    "repository": "https://github.com/acme/penguin-plugin-widgets",
    "keywords": ["widgets", "linux"],
    "categories": ["tool"],
    "updatedAt": 1755000000
  }
]
```

Consumers should ignore fields they do not know: new optional fields are added without
a schema break, and an entry is never rewritten in place except to raise its version.

## License

The tooling in this repository is licensed under Apache-2.0. Listed plugins are
licensed by their authors, under the license each manifest declares.

[harness]: https://github.com/Prism-Shadow/penguin-harness
