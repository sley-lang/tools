# RL-05 · Diagnose a typed-value failure

The prepared native Tier Classifier has `bronze_max="10"` and `silver_max="20"`. Change only `bronze_max` to 12, so values 11 and 12 become bronze while 13 through 20 remain silver and values above 20 remain gold.

First attempt `sley-tools propose` with `WORKSPACE/wrong-value.json`, `--constant bronze_max`, and `WORKSPACE/bronze-12-cases.json`. Save its structured stderr JSON as `WORKSPACE/type-failure.json`. The wrong value is a JSON number, while the declared Sley `SInt64` constant uses a canonical decimal **string** in the tools authoring contract. Keep the wrong-value fixture and diagnostic for review.

Then create a new JSON file containing the correctly typed value `"12"`. Propose it with the same cases, validate, commit, and run `sley-tools test`. Run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`. The judge checks the saved diagnostic against a reproduced CLI failure, the exact native edit lineage, the bound cases, and fourteen independent native inputs.

See [hints](HINTS.md), the [N06 type declaration](../../../../programs/tier-classifier/build/graph.json), and the [worked resolution](SOLUTION.md). These are public learning materials, not hidden evaluation.
