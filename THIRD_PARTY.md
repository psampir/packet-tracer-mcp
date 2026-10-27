# Third-party attributions

## This project

`packet-tracer-mcp` is released under the **MIT License** — see [LICENSE](LICENSE)
(`Copyright (c) 2026 Pawel Sampir`). The notices below cover third-party material consulted or
referenced by this project.

## Cisco Packet Tracer — API documentation

The IPC/Extensions API used by `bridge/bridge.js` — the `ipc` object with `ipc.network()`,
`ipc.appWindow()`, `ipc.systemFileManager()` and the `DeviceType` / `CONNECT_TYPES` /
`PortType` enums — is **documented by Cisco** in the Packet Tracer documentation set
(*Packet Tracer Scripting / Extensions API* sections) and in the *PT Script Modules* help that
ships with the application.

**No Cisco code is included in this repository.** Everything here is written from scratch against
the documented API surface: the JSON request/response shapes, the JavaScript module and all Python
code are original to this project. `bridge/bridge.js` is not a Cisco artifact — it is a script the
user pastes into *their own* Packet Tracer installation, where it runs inside their licensed copy.

"Packet Tracer" and "Cisco" are trademarks of Cisco Systems, Inc. This project is unofficial and is
not affiliated with, sponsored by or endorsed by Cisco.

## Python MCP SDK

The server is built on the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp`, MIT). No SDK code is
vendored: it is an ordinary dependency declared in `pyproject.toml` and pinned in `uv.lock`.

## Runtime dependencies

| Package | Purpose | License |
|---|---|---|
| [`mcp`](https://pypi.org/project/mcp/) | MCP protocol, stdio transport | MIT |
| [`pytest`](https://pypi.org/project/pytest/) (dev) | test runner | MIT |
| [`ruff`](https://pypi.org/project/ruff/) (dev) | linter / formatter | MIT |

The exact versions are pinned in `uv.lock`; each package's own license text applies.

## Project knowledge

The runtime caveats recorded in the README and `docs/protocol.md` (numeric `CONNECT_TYPES`,
`getOutput()` living on `ConsoleLine` rather than `TerminalLine`, `addDevice` auto-naming, watcher
events not firing in every environment) were verified against the locally installed Packet Tracer
9.0.1 documentation and observed behaviour on Fedora 44. They are factual API notes, not copied
documentation text.
