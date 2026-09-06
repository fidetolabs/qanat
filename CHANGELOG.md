# Changelog

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
