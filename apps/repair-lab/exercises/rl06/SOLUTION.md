# RL-06 worked resolution

From the repository root, with `WORKSPACE`, `CORE`, and `TOOLS` set after `lab.py start rl06`:

```sh
"$TOOLS" validate --project "$WORKSPACE/project" \
  2> "$WORKSPACE/stale-failure.json" || test "$?" -eq 4
"$TOOLS" inspect --project "$WORKSPACE/project" \
  > "$WORKSPACE/reinspection.json"
"$TOOLS" propose discard --project "$WORKSPACE/project"
"$TOOLS" propose --project "$WORKSPACE/project" \
  --max-blocking-findings 3 > "$WORKSPACE/fresh-proposal.json"
"$TOOLS" validate --project "$WORKSPACE/project"
"$TOOLS" commit --project "$WORKSPACE/project"
"$TOOLS" test --project "$WORKSPACE/project"
python3 apps/repair-lab/lab.py judge --workspace "$WORKSPACE" \
  --core "$CORE" --tools "$TOOLS"
```

The stale validation exits 4 with `ST_STALE_BASE` and category `stale_state`. The saved reinspection reports threshold 4 and the advanced accepted root. The new proposal names that root as its base and receives a different candidate ID. The accepted Sley program then allows a verified, completed, failure-free run with 3 blocking findings and rejects one with 4.
