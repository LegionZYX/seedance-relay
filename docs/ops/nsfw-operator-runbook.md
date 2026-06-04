# NSFW Operator Runbook

> Internal operator notes only. Do not expose this file in customer UI or public API docs.

## Scope

Relay does not add customer content moderation in alpha1. NSFW capability is controlled by signed-customer access to specific enabled models and the operator-only upstream BytePlus endpoint/profile used for those models. Model IDs are native BytePlus API IDs by default; optional admin aliases are allowed.

## Operating Facts

- API/SDK behavior may differ from console or playground behavior.
- Upstream endpoint/profile setup determines what succeeds.
- Console/playground can apply default filtering.
- Asset-library behavior and `asset://...` references may matter for some workflows.
- Upstream may still fail or block a task after submission.
- Audio settings can affect upstream success or failure.

## Relay Rules

- Do not add a separate SFW/NSFW toggle.
- Do not block customer prompt text in Relay.
- Keep NSFW prompt recipes and endpoint/profile assumptions operator-only.
- Use `enabled_models` to decide which signed customer can call which model ID or configured alias.
- Record sanitized upstream errors and request/task IDs for troubleshooting.

## Customer-Facing Behavior

Customers see native model IDs by default, or aliases only when the admin configured them and explicitly selected them for that customer, plus the native `content[]` request structure. They should not see upstream URLs, endpoint/profile names, upstream account labels, filter flags, keys, or internal prompt recipes.
