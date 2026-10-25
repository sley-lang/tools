# Sley Agent Connector

The Agent Connector exposes one explicitly selected local Sley project to an MCP client. A coding agent can inspect the accepted program, run typed inputs and tests, and, when write access is enabled for that connection, propose and commit supported edits.

It uses a local stdio server from the [MCP package](../../packages/mcp/). The project, `sley-tools` binary, and Sley core are chosen when the server starts; tool-call arguments cannot choose arbitrary project paths. Creation and Review require their own explicit startup configuration. See the [MCP setup and tool list](../../docs/MCP_V0.md) for the exact command and permissions.

The standalone MCP package and reference-client exchanges have been tested locally. A live coding-agent task is still an open acceptance check, so this page does not claim verified agent autonomy. [Install RC7](../../docs/INSTALL_RC7.md) · [Live-agent acceptance steps](../../docs/LIVE_AGENT_ACCEPTANCE.md)
