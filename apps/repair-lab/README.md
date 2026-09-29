# Sley Repair Lab: local public curriculum

The Repair Lab teaches native Sley authoring and repair through disposable, owner-controlled local workspaces. `lab.py` prepares each task and judges actual accepted Sley behavior through the selected `sley-tools` CLI and core. Python manages fixtures, assertions, and reset boundaries; it does not replace the native program's decisions. All eight local exercises are implemented: **RL-01 Create**, **RL-02 Boundary**, **RL-03 Branch**, **RL-04 Tests**, **RL-05 Type**, **RL-06 Stale state**, **RL-07 Authority**, and **RL-08 Portability**. The full curriculum is exercised from an isolated local install; native core test admission remains unavailable.

From the repository root, build or install `sley-tools` and set `CORE` to the verified Sley 2.0.1 binary. This example uses the local optional Lab adapter:

```sh
CORE=/path/to/sley-2.0.1-linux-x86_64/bin/sley
TOOLS=target/debug/sley-tools
LAB=apps/repair-lab/lab.py
"$TOOLS" lab list --adapter "$LAB"
"$TOOLS" lab start rl01 --adapter "$LAB" \
  --workspace /tmp/my-sley-rl01 --core "$CORE"
cat /tmp/my-sley-rl01/TASK.md
"$TOOLS" lab check --adapter "$LAB" \
  --workspace /tmp/my-sley-rl01 --core "$CORE"
```

The first check fails until you complete the task. `sley-tools lab check` invokes the adapter's `judge` command and preserves its JSON output and exit status: 0 on a pass, 1 on an unmet task, or 2 on a setup error. `sley-tools lab reset --adapter "$LAB" --workspace /tmp/my-sley-rl01 --core "$CORE"` resets the marked exercise. The copied `HINTS.md` and `SOLUTION.md` are public. Start any listed exercise in a new workspace. RL-02 through RL-04 prepare deliberate behavior flaws; RL-05 prepares an invalid edit value; RL-06 prepares a stale proposal after a separate accepted edit; RL-07 prepares a create-only grant and asks the learner to use portable import into a fresh authorized workspace; RL-08 transfers an edited native classifier and compares both manifests and runs. Each task specifies its real proposal, validation, run, test, and export path.

`reset` requires the lab marker and matching core/tools binary hashes. It refuses symlinked project paths, removes only `WORKSPACE/project` plus `WORKSPACE/recovered` for RL-07 or `WORKSPACE/copied` for RL-08, then recreates the prepared project for RL-02 through RL-08. Files outside the marked workspace, along with learner notes at its top level, remain intact. Reset deliberately discards changes *inside* those disposable projects. RL-04 through RL-08 keep top-level learner records; rerun the task to refresh any records tied to a newly created accepted root. `start` refuses to overwrite an existing directory.

The [public probe](../../evidence/repair-lab/rl01-rl08-v7.json) runs all eight exercises from new workspaces. It rejects wrong starts, missing boundary tests, invented diagnostics, a forged old-root reinspection, attempted edit under a create-only grant, and a fabricated imported execution result. It completes worked native commits, checks independent native inputs, captures a boundary failure despite a passing incomplete suite, preserves a structured type error, recovers from stale state and insufficient authority, and verifies edited native transfer. It reimports the RL-01 artifact and checks reset boundaries. Reproduce it with:

```sh
python3 scripts/probe_repair_lab.py \
  --tools target/debug/sley-tools \
  --core "$CORE" \
  --output evidence/repair-lab/rl01-rl08-v7.json
```

The [CLI launcher probe](../../evidence/repair-lab/launcher-v1.json) separately checks all eight listed IDs and one complete RL-01 path through `sley-tools lab`: an unfinished failure, native graph construction, a passing 16-case judge, and reset preserving learner notes. Reproduce it with:

```sh
python3 scripts/probe_repair_lab_launcher.py \
  --tools target/debug/sley-tools \
  --core "$CORE" \
  --adapter apps/repair-lab/lab.py \
  --graph examples/graph/range-predicate.json \
  --cases examples/graph/range-predicate-cases.json \
  --output evidence/repair-lab/launcher-v1.json
```

The task texts, hints, solutions, cases, and judge code are public learning materials. There are no claimed secret or uncontaminated evaluation cases in this repository. Independent owner-controlled evaluation and native core test admission remain outside this current local slice.
