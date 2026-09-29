# RL-05 worked resolution

From the repository root, with `WORKSPACE`, `CORE`, and `TOOLS` set after `lab.py start rl05`:

```sh
"$TOOLS" propose --project "$WORKSPACE/project" \
  --constant bronze_max --value-file "$WORKSPACE/wrong-value.json" \
  --tests "$WORKSPACE/bronze-12-cases.json" \
  2> "$WORKSPACE/type-failure.json" || test "$?" -eq 2
cat "$WORKSPACE/type-failure.json"
printf '"12"\n' > "$WORKSPACE/correct-value.json"
"$TOOLS" propose --project "$WORKSPACE/project" \
  --constant bronze_max --value-file "$WORKSPACE/correct-value.json" \
  --tests "$WORKSPACE/bronze-12-cases.json"
"$TOOLS" validate --project "$WORKSPACE/project"
"$TOOLS" commit --project "$WORKSPACE/project"
"$TOOLS" test --project "$WORKSPACE/project"
python3 apps/repair-lab/lab.py judge --workspace "$WORKSPACE" \
  --core "$CORE" --tools "$TOOLS"
```

The first proposal exits 2 with `ST_INPUT_INVALID`, category `input_shape`, and a message naming `bronze_max` as the wrongly typed value. It creates no candidate. The corrected proposal commits a native one-object graph edit. The accepted Sley program then classifies 12 as bronze, 13 as silver, and 21 as gold.
