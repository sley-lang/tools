# RL-04 · Add the missing boundary test

The prepared `WORKSPACE/project` is an accepted native Range Predicate with an exclusive upper endpoint, although the intended contract includes both endpoints. Its seven bound cases omit every exact upper endpoint, so `sley-tools test` passes while the defect remains.

Create `WORKSPACE/new-boundary-cases.json` using the public `sley-tools.graph-cases.v0` format. Include three cases for one valid interval `lower < upper`: `value=upper-1`, `value=upper`, and `value=upper+1`. Their expected results must follow the **inclusive** contract. You may choose any representable signed 64 bit bounds with room above the upper endpoint. Keep the native project unchanged; the task is to write a test that exposes the flaw.

Run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`. A pass means the new upper-endpoint case failed against the real flawed Sley graph, while the adjacent cases matched. The judge writes `WORKSPACE/boundary-failure.json` with the exact accepted root, case ID, expected result, observed native result, and new case-file digest. It checks the case expectations against an independent interval oracle before running them.

See [hints](HINTS.md), the [case format](../../../../programs/range-predicate/tests/cases.json), and the [worked resolution](SOLUTION.md). The tasks and judge are public learning materials; the extra checks are public rather than secret evaluation.
