# RL-05 hints

1. Read the `bronze_max` entry in the native authoring recipe and compare its `value_type` with the JSON type in `wrong-value.json`.
2. The CLI prints a JSON error to stderr with `code`, `category`, and `message`; save that object without changing its meaning.
3. The corrected value must be a JSON string containing canonical signed decimal text. The expected cases already describe the new threshold.
4. A successful proposal is only the first step. Validate and commit it, then run the native graph and its bound cases.
