# Agent notes

Facts that outlive a single session, for anyone (human or agent) working here.

## The integrity is npm's, and there is exactly one

- An entry's `integrity` is npm's own content hash: `sha512-` followed by the digest's
  base64 (88 characters ending `==`) — the value the registry reports as
  `dist.integrity`. There is no second, channel-level integrity: a `[[channels]]` table
  may only carry `kind` and `url` (`url` absent for `npm`), and the tooling rejects a
  channel `integrity` outright.
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
`typecheck`, `test`, `format:check`, `validate`, `check-npm` and `build` on the remote
box. Code reaches it by `git bundle` — and bundle the branch you actually pushed
(`refs/remotes/origin/<branch>` after a `git fetch origin`), not a stale
`refs/remotes/origin/main`, or the remote verifies the wrong commit and reports green
for code that fails CI.
