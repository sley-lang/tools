# RL-08 hints

1. Put the corrected threshold in a JSON string: `"12"`. Use `bronze-12-cases.json` in the proposal so the source's bound tests match the edit.
2. Export after the commit. `--bind-manifest` requires a new file directly under the source project's `artifact/` directory.
3. The source manifest's `artifact` points to its current accepted root. The imported project's `imported_source_artifact` records where its graph came from; its own current `artifact` is unset until another export.
4. Save the six command outputs after the source edit and destination import. The two roots and policies differ, while `run` returns the same `tier` and `invalid_configuration` fields.
