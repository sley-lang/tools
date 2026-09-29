# Sley Rules

Rules supplies small native decision programs that return an answer and the reasons behind it. Use [Release Readiness](../../programs/release-readiness/README.md) to evaluate supplied test, artifact, and finding facts; use [Resource Admission](../../programs/resource-admission/README.md) to check whether a requested job fits available workers and memory.

The checked-in packages each contain an accepted native artifact, input examples, tests, and a program README. The decision logic executes in Sley. The CLI or an SDK supplies inputs and presents the result; it does not make the decision for the program.

For a first run, follow [Starter](../starter/README.md). For application integration, follow [Bridge](../bridge/README.md). These programs evaluate facts you supply; Release Readiness does not certify a real software release. [Current native-test status](../../docs/F0_NATIVE_TEST_STATUS_PUBLIC.md)
