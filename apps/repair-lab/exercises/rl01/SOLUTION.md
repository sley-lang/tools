# RL-01 worked resolution

The [public Range Predicate recipe](../../../../examples/graph/range-predicate.json) is a complete reference. It creates an entry branch for reversed bounds, then uses strict comparisons and a native Boolean union to determine inclusive membership. The [public cases](../../../../examples/graph/range-predicate-cases.json) cover both endpoints, outside values, reversed bounds, and signed limits.

From the repository root, with `WORKSPACE`, `CORE`, and `TOOLS` set:

```sh
"$TOOLS" init "$WORKSPACE/project" \
  --graph examples/graph/range-predicate.json \
  --tests examples/graph/range-predicate-cases.json --core "$CORE"
"$TOOLS" test --project "$WORKSPACE/project"
"$TOOLS" artifact export --project "$WORKSPACE/project" \
  --output "$WORKSPACE/project/artifact/lab-range.json" --bind-manifest
python3 apps/repair-lab/lab.py judge --workspace "$WORKSPACE" \
  --core "$CORE" --tools "$TOOLS"
```

The judge executes additional public boundary combinations against the accepted Sley graph, imports the exported artifact into a fresh disposable workspace, and repeats them. A solution that merely returns a fixed result for the first examples fails.
