# RL-08 worked resolution

From the repository root, with `WORKSPACE`, `CORE`, and `TOOLS` set after `lab.py start rl08`:

```sh
printf '"12"\n' > "$WORKSPACE/new-threshold.json"
"$TOOLS" propose --project "$WORKSPACE/project" \
  --constant bronze_max --value-file "$WORKSPACE/new-threshold.json" \
  --tests "$WORKSPACE/bronze-12-cases.json"
"$TOOLS" validate --project "$WORKSPACE/project"
"$TOOLS" commit --project "$WORKSPACE/project"
"$TOOLS" artifact export --project "$WORKSPACE/project" \
  --output "$WORKSPACE/project/artifact/edited.json" --bind-manifest
"$TOOLS" artifact import "$WORKSPACE/project/artifact/edited.json" \
  "$WORKSPACE/copied" --core "$CORE"
"$TOOLS" inspect --project "$WORKSPACE/project" > "$WORKSPACE/source-inspection.json"
"$TOOLS" inspect --project "$WORKSPACE/copied" > "$WORKSPACE/destination-inspection.json"
"$TOOLS" test --project "$WORKSPACE/project" > "$WORKSPACE/source-test.json"
"$TOOLS" test --project "$WORKSPACE/copied" > "$WORKSPACE/destination-test.json"
"$TOOLS" run --project "$WORKSPACE/project" --input "$WORKSPACE/input-12.json" \
  > "$WORKSPACE/source-run.json"
"$TOOLS" run --project "$WORKSPACE/copied" --input "$WORKSPACE/input-12.json" \
  > "$WORKSPACE/destination-run.json"
python3 apps/repair-lab/lab.py judge --workspace "$WORKSPACE" \
  --core "$CORE" --tools "$TOOLS"
```

The new threshold places 12 in bronze. The exported artifact contains accepted native objects and a declared source root, while the destination uses new workspace and policy identities. Both manifest and execution observations are preserved separately; the destination's import is a fresh core-validated native commit.
