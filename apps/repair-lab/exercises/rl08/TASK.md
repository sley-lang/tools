# RL-08 · Transfer an edited native program

The prepared Tier Classifier is an accepted native Sley graph with `bronze_max="10"`. Change only that constant to `"12"` through proposal, separate validation, and commit using `WORKSPACE/bronze-12-cases.json`. Run its cases and `WORKSPACE/input-12.json`; value 12 should now be bronze.

Export the **changed accepted version** to `WORKSPACE/project/artifact/edited.json` with `--bind-manifest`. Import that file into a new `WORKSPACE/copied` project using the selected core. Test and run the imported project with the same typed input. Save the JSON output of `inspect`, `test`, and `run` for both projects at the top level of the lab workspace:

| Source | Imported copy |
|---|---|
| `source-inspection.json` | `destination-inspection.json` |
| `source-test.json` | `destination-test.json` |
| `source-run.json` | `destination-run.json` |

Run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`. The judge checks the source's bound manifest and artifact digest, the imported manifest's source binding, distinct workspace and policy roots, saved observations, and fourteen independent native inputs in each project. The imported root differs from the source root; the typed behavior matches.

See [hints](HINTS.md), the [native artifact contract](../../../../docs/ARTIFACT_V0.md), and the [worked resolution](SOLUTION.md). These are public learning materials.
