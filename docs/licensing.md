# Licensing

A listed plugin must be licensed such that the people who install it may actually use
it. We accept OSI-approved licenses plus CC0, and we check the identifier against a
closed list rather than against all of SPDX — a license this repository cannot fully
read is one a human should look at.

## Accepted identifiers

`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `MPL-2.0`,
`LGPL-3.0-only`, `LGPL-3.0-or-later`, `GPL-2.0-only`, `GPL-3.0-only`, `GPL-3.0-or-later`,
`AGPL-3.0-only`, `AGPL-3.0-or-later`, `BlueOak-1.0.0`, `Unlicense`, `CC0-1.0`.

Flat combinations are accepted too: `MIT OR Apache-2.0`, `Apache-2.0 AND CC0-1.0`.

Not accepted: parenthesised expressions, the `+` suffix (`GPL-2.0+`), `WITH` exceptions,
`UNLICENSED`, "all rights reserved", and anything not on the list. If your project needs
one of these, open an issue rather than a submission — the list is meant to grow when
there is a real case.

## Transport Ship 1.4.0

The maintainers approved the specific GitHub Release archive of
`@penguinharness/harbor-pulse@1.4.0` from the apps repository under its published
declaration, `SEE LICENSE IN THIRD-PARTY-NOTICES.txt`. The original Transport Ship
game has no stated license. Apache-2.0 covers the PenguinHarness workbench and audio
adapter, and MIT covers Three.js. The entry links to the notices and pins the
reviewed source, release channel and SHA-512. All other packages and versions
follow the ordinary license rules above. [Package notices](https://github.com/Prism-Shadow/penguin-harness-apps/blob/3d0ada9e67abf495ea3c47ea2835c0644d671444/plugins/harbor-pulse/THIRD-PARTY-NOTICES.txt)

## What must agree

The `license` in your manifest, the `license` in the published `package.json`, and the
license file in your repository must all say the same thing. CI checks the first two;
the third is part of review.

The authors and copyright years in the license file should be correct, and the package
must not ship material you do not have the right to distribute.
