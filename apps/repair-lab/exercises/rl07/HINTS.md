# RL-07 hints

1. Compare the `authority.allowed_mutation_classes` in the first `inspect` result with the kind of native edit the proposal needs.
2. `validate` returns `ST_PERMISSION_DENIED` and `CANDIDATE_VALIDATION_CAPABILITY_DENIED`. The original accepted root does not change.
3. A portable artifact contains the program's verified objects, not its principal grant or policy root. Importing it creates new workspace and policy identities.
4. Record the new project's inspection before its edit. Its `replace_entity_version_allowed` field should be true, and its policy root should differ from the limited project's policy root.
