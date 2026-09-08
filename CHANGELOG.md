# Changelog

## 0.1.1 — 2026-09-08

Two console fixes, both found by using it.

**The backtest run form could not be scrolled.** `#editor-body` carries the
flex/overflow rules that let the alpha editor scroll; `#runner-body` never got
the matching rule, so the form kept its full content height whatever the window.
The wheel did nothing and `run it` sat below the fold, out of reach on any
display under about 1030px.

**An alpha could be pointed at a table that holds no prices.** Every script on
the shelf reads one table with a symbol, a date and a price, but the console
offered every table in the project and `save_alpha` checked only that the table
existed. Wiring one to something like a market-wide regime series saved cleanly,
passed `check`, then died once per as-of date mid-replay on a bare
`KeyError: 'date'` naming neither the table nor the column. The save now refuses
it and says which column is missing and what the table actually holds.

## 0.1.0 — 2026-09-05

First packaged release. Published to PyPI as
[`qanat-fdtl`](https://pypi.org/project/qanat-fdtl/0.1.0/).

### Provenance note

The git history of this repository was rewritten and republished after 0.1.0
was uploaded to PyPI. Two consequences, neither of which affects the published
package:

1. **The attested commit no longer exists.** PyPI's build attestation for 0.1.0
   names commit `f878c9f8f018bfc99e96a02544cb8699f7f8e056`, which was destroyed
   by the rewrite. An automated provenance check against that hash will not
   resolve. The attestation itself is signed and immutable, so it cannot be
   corrected.

2. **The published artifacts were never rebuilt or replaced.** What is on PyPI
   is exactly what was uploaded on 2026-09-05.

The source tree is unchanged, and that is verifiable. The `v0.1.0` tag points at
a commit whose tree is byte-identical to the published sdist:

```bash
git checkout v0.1.0
pip download qanat-fdtl==0.1.0 --no-binary :all: --no-deps
tar xzf qanat_fdtl-0.1.0.tar.gz
diff -r qanat_fdtl-0.1.0 . -x .git -x PKG-INFO   # no differences
```

PyPI file digests for 0.1.0:

| file | sha256 |
| --- | --- |
| `qanat_fdtl-0.1.0.tar.gz` | `42cbcb9939f5f0486fb665c918a73700ae4b41956119ffabaeff41f9081e2325` |
| `qanat_fdtl-0.1.0-py3-none-any.whl` | `da87e5baff12423a13445d9b8b76150e9c0ccceca2a57cf6db3cbe44196c0dfa` |
