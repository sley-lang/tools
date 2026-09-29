# RL-04 hints

1. Run `sley-tools test --project WORKSPACE/project`. Passing only means the bound seven cases passed.
2. Inspect `WORKSPACE/missing-upper-cases.json`: it checks below and above but never the exact upper endpoint.
3. Use the same result fields as N07. At `value=upper`, inclusive membership is true, `above_upper` is false, and `excluded_upper` is false.
4. The judge expects one interval and the three adjacent values in signed decimal string form. It will execute your file against the accepted native root and record the failing case.
