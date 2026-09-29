# RL-02 hints

1. Run the prepared project at `value=upper`; its initial result has `within_range=false` and `excluded_upper=true`.
2. The graph's named constant `exclude_upper_endpoint` controls that policy. Write a JSON file containing `false`.
3. Submit the change with `--tests WORKSPACE/inclusive-cases.json`, validate it, then commit. Inspect the new root and run the public cases. The `above_upper` flag stays false at the exact endpoint.
