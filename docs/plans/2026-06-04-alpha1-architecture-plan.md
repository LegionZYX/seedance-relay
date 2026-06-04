# Superseded Alpha1 Architecture Plan

> Status: superseded. Do not implement this file.

The active alpha1 execution SPEC is:

```text
docs/plans/2026-06-04-alpha1-runtime-proxy-spec.md
```

This earlier architecture draft is intentionally replaced because the owner refined the scope:

- no separate customer SFW/NSFW setting,
- no Relay-side customer content censorship,
- no real-human authorization workflow inside Relay,
- admin controls focus on customer balance, direct consumption multiplier, enabled model list, credentials, audit, and operational safety,
- generated video delivery should be proxy-only by default and should not expose BytePlus storage URLs.

For execution, use only the active SPEC and the ops runbooks under `docs/ops/`.
