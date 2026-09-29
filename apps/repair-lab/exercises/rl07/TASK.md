# RL-07 · Resolve an insufficient local grant

The prepared Release Readiness program was constructed in a disposable workspace whose accepted policy allows creation but does not grant `ReplaceEntityVersion`. The program runs and tests normally. The task is to change `max_blocking_findings` from 0 to 2 without changing this existing workspace's policy or pretending a different principal authorized its edit.

Save `sley-tools inspect --project WORKSPACE/project` as `WORKSPACE/limited-inspection.json`. Propose threshold 2 in that project, run `validate`, and save its structured stderr JSON as `WORKSPACE/permission-failure.json`. Inspect the unchanged accepted root. The policy reported by `inspect` is read from the native repository, and the core's candidate result identifies the missing grant.

Use the legitimate local setup path: export the accepted program to `WORKSPACE/portable.json`, import it into a new `WORKSPACE/recovered` project with its own normal development grant, and save that new project's first inspection as `WORKSPACE/recovered-inspection.json`. In the new project, propose threshold 2, validate, commit, and test. Write `WORKSPACE/authority-explanation.json` with these fields:

```json
{
  "denied_mutation_class": "ReplaceEntityVersion",
  "source_policy_root": "POLICY_ROOT_FROM_LIMITED_INSPECTION",
  "destination_policy_root": "POLICY_ROOT_FROM_RECOVERED_INSPECTION",
  "resolution": "fresh_workspace_import"
}
```

Run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`. It checks the real accepted grants, the denial, source and destination roots, portable artifact lineage, and native behavior in both projects. The artifact carries program objects without the source workspace's authority.

See [hints](HINTS.md), the [portable artifact contract](../../../../docs/ARTIFACT_V0.md), and the [worked resolution](SOLUTION.md). These are public learning materials in an owner-controlled local lab.
