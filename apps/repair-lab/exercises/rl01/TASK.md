# RL-01 · Create a native range predicate

Build a new `graph_v0` Sley program named `range-predicate`. It takes signed 64 bit `value`, `lower`, and `upper` inputs and returns four Boolean fields: `within_range`, `invalid_bounds`, `below_lower`, and `above_upper`. Both endpoints are inclusive. When `lower > upper`, only `invalid_bounds` is true. A value below or above valid bounds sets its corresponding reason flag.

`starter-recipe.json` contains the interface and intentionally has no blocks. Complete a recipe, initialize `WORKSPACE/project` with `sley-tools init ... --graph ... --tests ... --core ...`, run its cases, and export a **manifest-bound** native artifact under `WORKSPACE/project/artifact/`. Use the selected Sley 2.1.1 core. The first examples are `[-5,-5,5] → within`, `[5,-5,5] → within`, `[-6,-5,5] → below`, and `[0,5,-5] → invalid bounds`. The public judge also checks other boundaries and a fresh artifact import.

Run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`. The judge requires a newly constructed native graph, its current artifact and manifest binding, published cases, and exact native behavior. Importing an already built artifact does not satisfy the create lesson.

See [hints](HINTS.md), the [graph authoring profile](../../../../docs/GRAPH_AUTHORING_V0.md), and the [worked resolution](SOLUTION.md). These are public learning materials; the judge's checks are public too.
