# Sley Starter

Starter is the shortest path from an installed Sley core to a program you can run and change. Its CLI creates a local workspace, commits a native program, runs it, checks examples, and exports the artifact. Start here if you want to learn the basic workflow.

With `CORE` set to your verified Sley 2.1.1 binary and `sley-tools` installed:

```sh
sley-tools doctor --core "$CORE"
sley-tools init my-first-program --template release-readiness --core "$CORE"
sley-tools run --project my-first-program --input my-first-program/inputs/ready.json
sley-tools test --project my-first-program
sley-tools inspect --project my-first-program
```

`sley-tools --version` prints the toolkit version and the tested core commit; `sley-tools --help` lists every command form. Both write plain text and exit 0, while all other commands keep their JSON reports.

The example's release decision runs in its accepted Sley graph. To change the permitted number of blocking findings, use `sley-tools edit --project my-first-program --max-blocking-findings 2`, then run the examples again. [The authoring guide](../../docs/AUTHORING_V0.md) explains the separate propose, validate, and commit steps; [artifact transfer](../../docs/ARTIFACT_V0.md) explains export and import. The [Release Readiness program](../../programs/release-readiness/README.md) has its own behavior and package documentation.

The selected public core can execute the program, while core-owned native `TestCase` admission remains unavailable. `sley-tools test` checks external assertions against native execution. [Install RC7](../../docs/INSTALL_RC7.md) · [Current test status](../../docs/F0_NATIVE_TEST_STATUS_PUBLIC.md)
