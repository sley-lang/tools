# Sley Repair Lab

Repair Lab is a local set of eight exercises for learning native Sley authoring and repair. Tasks cover creation, boundary mistakes, branch changes, tests, type errors, stale edits, authority, and portable artifact transfer. Each exercise starts in a disposable workspace and checks what the accepted Sley program actually does.

```sh
sley-tools lab list --adapter apps/repair-lab/lab.py
sley-tools lab start rl01 --adapter apps/repair-lab/lab.py \
  --workspace /tmp/my-sley-rl01 --core "$CORE"
```

The [Lab guide](../../apps/repair-lab/README.md) has the complete start, check, and reset commands. Its tasks, hints, and solutions are public learning material. The Python adapter prepares and judges the exercise; native program behavior runs in Sley. Native core `TestCase` admission remains pending on the selected public core.
