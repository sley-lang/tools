# RL-03 · Repair a classification branch

The prepared `WORKSPACE/project` contains an accepted native Tier Classifier. Its bronze threshold is 10 and silver threshold is 20, but the middle-tier branch has been disabled. As a result, values 11 through 20 wrongly become gold. Repair the named native `middle_tier_enabled` constant through proposal, separate validation, and commit. Preserve bronze results for values at or below 10, gold results above 20, and the invalid-configuration branch.

The result record has `tier` (`"0"` bronze, `"1"` silver, `"2"` gold, `"-1"` invalid configuration) and `invalid_configuration`. `WORKSPACE/correct-cases.json` is the public expected behavior; `WORKSPACE/disabled-middle-cases.json` describes the starting flaw. Inspect the accepted root before editing and run the native cases after commit. The judge verifies the original imported seed lineage, the corrective native commit, all category boundaries, and unrelated branches through independent inputs.

Run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`. See [hints](HINTS.md), the [N06 program](../../../../programs/tier-classifier/README.md), and the [worked resolution](SOLUTION.md). These are public learning materials.
