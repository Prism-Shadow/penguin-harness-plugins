# Agent notes

Facts that outlive a single session, for anyone (human or agent) working here.

## The integrity is npm's, and there is exactly one

- An entry's `integrity` is npm's own content hash: `sha512-` followed by the digest's
  base64 (88 characters ending `==`) — the value the registry reports as
  `dist.integrity`. There is no second, channel-level integrity: a `[[channels]]` table
  may only carry `kind` and `url` (`url` omitted for `npm` — the build writes the
  registry's tarball address into `index.json`), and the tooling rejects a channel
  `integrity` outright.
- An entry's directory key is that digest in base64url — `+` as `-`, `/` as `_`, no
  padding: 86 characters, every one of them path-safe. The ordinary entry is keyless
  (`…/<version>/manifest.toml`, no key segment): it claims its name and version have
  exactly one content, and validation refuses a keyless entry beside any other entry of
  the same name and version. The keyed form exists for a store holding an older content
  beside a fresh one while it hot-updates.
- Every channel must serve byte-identical tarballs. A GitHub-release channel publishes
  the same bytes `npm pack` produces; CI hashes what each URL serves against the one
  `integrity`. There is no archive-vs-lock content comparison anywhere: the lock (npm
  channel) and the tarball hash (URL channels) pin the content.

## The path rules are shared with another repository

`plugin-index/src/entry.ts` computes `packages/…` paths by the same rule as
PenguinHarness's plugin store (`scripts/plugin-entry.mjs`), and the two share test
vectors across repositories. Changing the hash format, the key encoding or the bucket
rule here breaks the harness's plugin store unless the same change lands there at the
same time. The base64url key and the keyless entry shape are newer here than there;
syncing the harness's copy is pending on the harness side.

## The index must stay readable by every harness in the field

- A PenguinHarness release reads `index.json` with one strict reader and refuses the whole
  document over a single row it does not accept: its market then lists no remote plugin at
  all. [#70](https://github.com/Prism-Shadow/penguin-harness-plugins/pull/70) did exactly
  that — npm channels without a `url` emptied the market of every 0.2.19 deployment.
- `pnpm check-harness` parses the built index with the reader of every release from
  `READER_FLOOR` in `src/harness-readers.ts` up (each release's own published
  `@prismshadow/penguin-core/plugin-index`, found on the registry at each run), plus the
  harness's `main` when `HARNESS_MAIN_READER` names a checkout's
  `packages/core/src/plugin/index-document.ts` — CI checks one out into `.harness-main/`.
  CI runs it after the build, `main` included; Deploy and Nightly run it before they publish,
  with the released readers only, so an unreleased harness change never stops publishing.
- A new field or shape in `index.json` has to be one every listed reader already accepts.
  Raising the floor is a decision to stop supporting the releases below it, never a way to
  turn the check green.
- Every channel in `index.json` carries a `url`; the npm channel's is the registry's
  tarball address of that name and version (`src/lib/channels.ts`), and `check-npm`
  compares it with the registry's `dist.tarball`.

## How CI judges a submission

- `pnpm validate` takes `BASE_REF` and `PR_AUTHOR` from the environment. A rename
  (git's `-M` detection) and a deletion are both refused — except for a maintainer
  (listed in `src/lib/vocabulary.ts`), who may carry a schema migration that moves
  every entry at once.
- `pnpm check-npm` only touches entries this PR changes, and only ones with an `npm`
  channel; a github-only entry is verified by fetching its URL and hashing the tarball.
- The published index is rebuilt on merge (`Deploy` → Pages) and re-served every six
  hours as the `nightly` release's `index.json` asset.

## Verifying a change before pushing

This repository has no local build/test machine: run `pnpm install --frozen-lockfile`,
`typecheck`, `test`, `format:check`, `validate`, `check-npm`, `build` and `check-harness`
on the remote box. Code reaches it by `git bundle` — and bundle the branch you actually pushed
(`refs/remotes/origin/<branch>` after a `git fetch origin`), not a stale
`refs/remotes/origin/main`, or the remote verifies the wrong commit and reports green
for code that fails CI.
