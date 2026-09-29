# RL-06 hints

1. `validate` reports `ST_STALE_BASE` because the proposal's `base_root` no longer equals the inspected accepted root.
2. Save the full `inspect` JSON while the accepted threshold is 4. Its root is the base for the replacement proposal.
3. Use `sley-tools propose discard --project WORKSPACE/project` to clear the old local proposal. Discarding it does not roll back the accepted edit to 4.
4. Propose threshold 3 again, then validate and commit. Check the replacement proposal's `candidate_id` and `base_root` before committing.
