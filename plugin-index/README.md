# @prismshadow/penguin-plugin-index

The plugin entry format, as code: what the PenguinHarness plugin index
([penguin-plugins](https://github.com/Prism-Shadow/penguin-plugins)) checks, and what the
harness computes when a package enters a machine's plugin store. Both sides import this
package so that the value they compare — an entry's `integrity` — is computed by one
implementation.

## An entry

```
[<@scope>/]<bucket>/<name>/<version>/<first 16 hex digits of integrity>/
  manifest.toml        the index manifest; `integrity` required
  package-lock.json    the lock the package is installed from
  package/             the installed package, every dependency inside it   (store only)
  .stored              the completion marker                                (store only)
```

The index repository keeps the first two under `packages/`; a store keeps what it runs
under its own `packages/`. The bucket (`bucketOf`, `nameDir`) is read off the name without
its scope: `1` or `2` for a name of that length, `3/<first character>` for three
characters, `<characters 1–2>/<characters 3–4>` otherwise.

## What it exports

| Export                                        | What it is                                                                                    |
| --------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `packTar`, `packArchive`, `archiveChunks`     | The deterministic archiver (ustar; sorted, epoch mtimes, 0755/0644). `packArchive` gzips.     |
| `packageFiles`, `packageIntegrity`            | An installed package's `package/` files (hoisted dependencies folded in) and their integrity. |
| `installFromLock`, `resolveLock`, `checkLock` | The one install an entry is reproduced by (`npm ci` with fixed flags), and the lock rules.    |
| `parseManifest`, `manifestPathFor`            | The manifest schema, including "the path spells the name, the version and the key".           |
| `entryDir`, `entryKey`, `parseEntryPath`      | The path rules on their own.                                                                  |

## The integrity

`sha256-<hex>` of the uncompressed archive over the entry's `package/` directory, every
path starting with `package/`. The archive format is specified at the top of
`src/archive.ts`; any change to it changes every integrity and is a breaking release.
