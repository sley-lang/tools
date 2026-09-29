# RL-02 · Correct an upper endpoint

The prepared native Range Predicate deliberately excludes the upper endpoint. The intended contract is inclusive: below, at, and above a valid upper bound must produce distinct results. Correct the named native `exclude_upper_endpoint` constant from `true` to `false` through proposal, separate validation, and commit. Preserve the reversed-bound branch and unrelated membership behavior.

The prepared project is `WORKSPACE/project`. `WORKSPACE/inclusive-cases.json` contains public expected results for the corrected policy; `WORKSPACE/excluded-cases.json` describes the starting policy. Use `sley-tools inspect` to see the exact accepted root and constant before editing. The judge checks native history, unchanged root through proposal/validation as recorded by the CLI, the corrected accepted root, the five result fields, and independent interval cases.

Run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`. See [hints](HINTS.md), the [catalog program](../../../../programs/range-predicate/README.md), and the [worked resolution](SOLUTION.md). All checks are public learning checks.
