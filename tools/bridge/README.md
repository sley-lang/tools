# Sley Bridge

Bridge lets a Python or TypeScript application call an accepted Sley program without implementing the Sley protocol itself. Use it when an application needs a native decision or calculation, such as Release Readiness, Resource Admission, or a graph you built.

The Python and TypeScript SDKs open a local project or verified artifact, send typed inputs to the shared Rust backend, and return the native result with execution metadata. Signed 64 bit integers stay exact: Python uses `int`, TypeScript uses `bigint`, and the wire uses decimal strings. Both SDKs also support the bounded native proposal, validation, and commit lifecycle.

Code and examples: [Python package](../../packages/python/), [TypeScript package](../../packages/typescript/), and the [local RPC and SDK guide](../../docs/RPC_V0.md). The [Range Predicate Python example](../../programs/range-predicate/README.md) shows an artifact called from an application. The SDKs are optional RC4 packages; they require the local `sley-tools` CLI and a compatible Sley core. [Install RC4](../../docs/INSTALL_RC4.md)
