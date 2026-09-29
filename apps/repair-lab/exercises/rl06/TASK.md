# RL-06 · Recover from a stale proposal

The prepared native Release Readiness project contains a proposal to set `max_blocking_findings` to 3. That proposal was made at the original accepted root. A separate supported `sley-tools edit` then changed the threshold to 4, advancing the accepted root while leaving the first proposal pending. `WORKSPACE/stale-proposal.json` preserves the original proposal for comparison.

Run `sley-tools validate --project WORKSPACE/project` and save its structured stderr JSON to `WORKSPACE/stale-failure.json`. Inspect the current project and save the JSON result to `WORKSPACE/reinspection.json`. The two roots should differ. Discard the stale proposal through `sley-tools propose discard`, then propose threshold 3 from the inspected current state and save that command's JSON result to `WORKSPACE/fresh-proposal.json`. Validate, commit, test, and run `python3 apps/repair-lab/lab.py judge --workspace WORKSPACE --core CORE --tools TOOLS`.

The judge requires the new proposal to name the advanced root as its base. It checks the native edit history, the three saved CLI records, the bound test run, and independent native execution around the new threshold. A silent overwrite or reused candidate cannot pass.

See [hints](HINTS.md), the [Release Readiness contract](../../../../programs/release-readiness/README.md), and the [worked resolution](SOLUTION.md). These are public learning materials.
