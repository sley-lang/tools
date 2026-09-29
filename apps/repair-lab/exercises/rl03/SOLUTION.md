# RL-03 worked resolution

From the repository root, with `WORKSPACE`, `CORE`, and `TOOLS` set after `lab.py start rl03`:

```sh
"$TOOLS" inspect --project "$WORKSPACE/project"
printf 'true\n' > "$WORKSPACE/enable-middle.json"
"$TOOLS" propose --project "$WORKSPACE/project" \
  --constant middle_tier_enabled \
  --value-file "$WORKSPACE/enable-middle.json" \
  --tests "$WORKSPACE/correct-cases.json"
"$TOOLS" validate --project "$WORKSPACE/project"
"$TOOLS" commit --project "$WORKSPACE/project"
"$TOOLS" test --project "$WORKSPACE/project"
python3 apps/repair-lab/lab.py judge --workspace "$WORKSPACE" \
  --core "$CORE" --tools "$TOOLS"
```

The preparation path imports the accepted [N06 native artifact](../../../../programs/tier-classifier/artifact/native-graph.json) into a disposable workspace and commits a one-object edit that disables the silver branch. The correction is another one-object native commit. The [catalog probe](../../../../evidence/catalog/tier-classifier-v1.json) independently verifies both branch policies and the unchanged bronze and gold categories.
