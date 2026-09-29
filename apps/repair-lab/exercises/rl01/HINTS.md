# RL-01 hints

1. Put the reversed-bound check in `entry` and branch before the normal membership checks.
2. In the valid branch, `value < lower` and `value > upper` are the two strict outside conditions. Their negated union includes both endpoints.
3. The graph builder accepts `record_new` with fields in the declared result order. `tests/cases.json` in the repository shows the public case format. Use `artifact export --bind-manifest` after the program passes.
