# RL-04 worked resolution

The public [worked case file](solution-cases.json) chooses `lower=-7`, `upper=7`, and values 6, 7, and 8. The expected results follow the inclusive interval contract. From the repository root after `lab.py start rl04`:

```sh
cp apps/repair-lab/exercises/rl04/solution-cases.json \
  "$WORKSPACE/new-boundary-cases.json"
"$TOOLS" test --project "$WORKSPACE/project"
python3 apps/repair-lab/lab.py judge --workspace "$WORKSPACE" \
  --core "$CORE" --tools "$TOOLS"
cat "$WORKSPACE/boundary-failure.json"
```

The old bound suite passes, yet the new case at 7 expects `within_range=true` and the flawed native program computes `false` with `excluded_upper=true`. The two adjacent cases match. The judge saves this actual failure diagnostic and leaves the accepted root unchanged. RL-02 covers correcting the program after a boundary flaw has been demonstrated.
