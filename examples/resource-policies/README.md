# Vendor-neutral resource policies

The Core requests a semantic capability and an assurance mode. It does not
select a provider, model family, or host route. A deployment or host may map
those capabilities to the routes it currently exposes; the mapping is
resolution evidence, not an observed execution receipt.

- [`neutral-run.json`](neutral-run.json) is safe to pass to a run compiler.
- [`host-route-map.json`](host-route-map.json) shows a host-owned route map
  using opaque deployment route identifiers.

The three evidence layers remain separate:

```text
requested capability/policy -> resolved host route -> actual host observation
```

An absent host observation stays `actual: null` and `actual_state: unresolved`.
