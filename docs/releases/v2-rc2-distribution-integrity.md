# RC2 distribution integrity contract

This document records the release-boundary contract for the All in Luna and
Research Routes distributions. It is an RC qualification artifact only. It
does not authorize a stable tag, a stable release, a push, or publication.

## Version and tag source

`distributions/distribution-manifest.json` is the source consumed by the
distribution builder. Each distribution declares one semantic RC version and
the builder requires the exact namespaced tag `<plugin_name>/<version>`.
The manifest is checked against each source plugin metadata file before an
artifact is emitted, so a stale plugin version cannot be silently repackaged.

The validator accepts any numbered `-rc.N` candidate and derives expected
versions from the manifest. This keeps the RC2 lane independent from a
previous RC1 literal while allowing the integration lane to advance the
version source without changing distribution code.

## Artifact integrity

Both artifacts carry source commit, tree, parent, ref, a canonical source
inventory, a release manifest, and the project license. Every canonical file
is re-hashed in the built artifact during validation. Source and overlay
symlinks are rejected, overlay files must be on the declared allowlist, and
the Research Routes artifact retains its Pack-local runtime without copying a
duplicate shared runtime.

## Co-installation and release posture

The standalone marketplace manifests resolve to their own plugin roots. The
installation validator copies both artifacts into isolated namespaced
destinations and checks that their identities, versions, runtime roots, skill
entrypoints, canonical inventories, and release manifests remain distinct and
co-installable.

The required checks are:

```text
python scripts/validate_distributions.py
python scripts/validate_installations.py
python -m pytest tests/test_distributions.py tests/integration/test_distribution_integrity.py -q
```

Any missing, stale, mismatched, or blocked evidence remains a validation
failure or unknown; it is never promoted to a release claim by the builder.
