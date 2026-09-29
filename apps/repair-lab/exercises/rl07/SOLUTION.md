# RL-07 worked resolution

From the repository root, with `WORKSPACE`, `CORE`, and `TOOLS` set after `lab.py start rl07`:

```sh
"$TOOLS" inspect --project "$WORKSPACE/project" \
  > "$WORKSPACE/limited-inspection.json"
"$TOOLS" propose --project "$WORKSPACE/project" \
  --max-blocking-findings 2
"$TOOLS" validate --project "$WORKSPACE/project" \
  2> "$WORKSPACE/permission-failure.json" || test "$?" -eq 4
"$TOOLS" inspect --project "$WORKSPACE/project"
"$TOOLS" artifact export --project "$WORKSPACE/project" \
  --output "$WORKSPACE/portable.json"
"$TOOLS" artifact import "$WORKSPACE/portable.json" \
  "$WORKSPACE/recovered" --core "$CORE"
"$TOOLS" inspect --project "$WORKSPACE/recovered" \
  > "$WORKSPACE/recovered-inspection.json"
"$TOOLS" propose --project "$WORKSPACE/recovered" \
  --max-blocking-findings 2
"$TOOLS" validate --project "$WORKSPACE/recovered"
"$TOOLS" commit --project "$WORKSPACE/recovered"
"$TOOLS" test --project "$WORKSPACE/recovered"
```

Copy the two `authority.policy_root` values from the saved inspections into `WORKSPACE/authority-explanation.json` using the structure in [TASK.md](TASK.md), then run the lab judge. The first `validate` exits 4 with `ST_PERMISSION_DENIED`; no local file edit, principal ID substitution, or policy rewrite is a remedy. The new project receives a grant through its own normal initialization, imports only the verified program, and commits the edit there.
