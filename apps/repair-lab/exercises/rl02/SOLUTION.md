# RL-02 worked resolution

From the repository root, with `WORKSPACE`, `CORE`, and `TOOLS` set after `lab.py start rl02`:

```sh
"$TOOLS" inspect --project "$WORKSPACE/project"
printf 'false\n' > "$WORKSPACE/include-upper.json"
"$TOOLS" propose --project "$WORKSPACE/project" \
  --constant exclude_upper_endpoint \
  --value-file "$WORKSPACE/include-upper.json" \
  --tests "$WORKSPACE/inclusive-cases.json"
"$TOOLS" validate --project "$WORKSPACE/project"
"$TOOLS" commit --project "$WORKSPACE/project"
"$TOOLS" test --project "$WORKSPACE/project"
python3 apps/repair-lab/lab.py judge --workspace "$WORKSPACE" \
  --core "$CORE" --tools "$TOOLS"
```

The prepared state was created by importing the accepted [N07 artifact](../../../../programs/range-predicate/artifact/native-graph.json) into a disposable workspace and committing the opposite policy. The correction restores inclusive behavior through another native one-object edit. The [catalog probe](../../../../evidence/catalog/range-predicate-v2.json) independently exercises the same regression and correction. No hidden evaluation claim is made from these public fixtures.
