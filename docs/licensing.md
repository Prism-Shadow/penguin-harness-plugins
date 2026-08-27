# Licensing

A listed extension must be licensed such that the people who install it may actually use
it. We accept OSI-approved licenses plus CC0, and we check the identifier against a
closed list rather than against all of SPDX — a license this repository cannot fully
read is one a human should look at.

## Accepted identifiers

`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `MPL-2.0`,
`LGPL-3.0-only`, `LGPL-3.0-or-later`, `GPL-3.0-only`, `GPL-3.0-or-later`,
`AGPL-3.0-only`, `AGPL-3.0-or-later`, `BlueOak-1.0.0`, `Unlicense`, `CC0-1.0`.

Flat combinations are accepted too: `MIT OR Apache-2.0`, `Apache-2.0 AND CC0-1.0`.

Not accepted: parenthesised expressions, the `+` suffix (`GPL-2.0+`), `WITH` exceptions,
`UNLICENSED`, "all rights reserved", and anything not on the list. If your project needs
one of these, open an issue rather than a submission — the list is meant to grow when
there is a real case.

## What must agree

The `license` in your manifest, the `license` in the published `package.json`, and the
license file in your repository must all say the same thing. CI checks the first two;
the third is part of review.

The authors and copyright years in the license file should be correct, and the package
must not ship material you do not have the right to distribute.
