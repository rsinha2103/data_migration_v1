# ADR 0010: Only confirmed checks run

Status: accepted. Supersedes the "derived checks are automatic" parts of ADR 0003 and ADR 0009.

## Decision
- **Nothing is checked unless the user has confirmed it.** The mandatory, length, data-type and key rules that the target structure implies are no longer run automatically. They are confirmed per kind under `from_mapping` in `checks.yaml`, each with a severity (`error` blocks the load, `warn` only reports); explicit checks under `checks:` are confirmed by being there.
- In the web interface the structure rules are proposals (one per kind, test-run on the staged data first, proposed as `warn` when the data already breaks them) and can also be set on the Checks tab ("Rules from your target structure": off / warning / error).
- With no confirmed check the **Check step runs nothing**: `run_checks` returns an empty list, no run-log step is recorded, and the UI/CLI say so ("No checks are confirmed yet, so nothing was run"). The flow card stays "NOT RUN" rather than pretending a clean pass.
- Because the load runs the confirmed checks first (ADR 0004), **with none confirmed the load can never be held back by a check**. Confirmed `error` checks still block it. The Loads tab says which of the two applies and has a Run load button (disabled until a destination exists); the Checks and Reconciliation tabs have Run buttons too.
- `from_mapping` is preserved when explicit checks are added or removed.
- The example projects confirm all four kinds so they keep demonstrating the full set.

## Consequences
Existing projects that relied on automatic structure checks must add `from_mapping` to `checks.yaml` (the shipped examples do). Mandatory fields that are not mapped yet still matter: they are listed on the Proposals tab and flagged as soon as the mandatory rule is confirmed.
