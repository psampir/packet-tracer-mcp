# Packet Tracer setup (click-by-click)

One-time work in the Packet Tracer GUI. Afterwards PT exposes the file-drop bridge
on its own every time it starts — nothing here has to be repeated per session.

> **Version note.** The labels below were verified against **Cisco Packet Tracer
> 9.0.1** on Fedora 44. Packet Tracer's Scripting UI is stable but not frozen:
> if a label differs slightly in your build, the tab order is the same.

## 0. Before you start

- Packet Tracer **9.0.1+** installed and licensed (this project uses only the
  documented Scripting/Extensions API).
- The MCP server installed at `/home/pawel/Code/Projects/packet-tracer-mcp`
  (`uv sync` there once — see the [README](../README.md#development)).
- This file open in an editor: [`bridge/bridge.js`](../bridge/bridge.js). You
  will paste its **entire** contents into PT.

## 1. Open (or create) a `.pkt`

Start Packet Tracer and open a lab file, or create a new one with
**File → New**. The bridge only ever acts on the **active** file/window, so keep
the lab you want to drive in front.

## 2. Register the Script Module

1. Menu bar → **Extensions** → **Scripting** → **Configure PT Script Modules…**
2. In the dialog that opens, click **Add** (older builds label it **New**) to
   create a module.

### General tab

| Field | Value |
|---|---|
| **ID** | `com.pawel.ptmcp` |
| **Name** | `pt-mcp` |
| **Startup** | **On Startup** — so the bridge comes up with PT, no clicking later |

### Script Engine tab

1. Click **Add** (or the **+** button) to add a script file to the module.
2. Name the file **`bridge.js`** — the name is cosmetic, but keeping it identical
   to the repository copy avoids confusion when you update it.
3. Paste the **whole** of `bridge/bridge.js` into the editor pane (it is ES5 /
   QtScript: `var` and `function` only — do not "modernise" it).
4. Click **Apply** / **OK** to close the editor.

### Security Privileges tab

The bridge is sandboxed by PT and needs these privileges; anything you leave out
makes the matching op fail with a privilege/access-denied error:

| Privilege | Why the bridge needs it |
|---|---|
| **Application** | run the module at all, read the PT version, write to the PT log |
| **File Operations** | create/read/delete the `jobs/` and `results/` files, and save `.pkt`s |
| **Get Network Info** | list devices and links in the active file |
| **Change Network Info** | add/remove devices, create links, power devices on/off, send CLI |
| **Miscellaneous UI** *(optional)* | `ipc.appWindow().writeToPT(...)` log lines, i.e. `[pt-mcp] …` output in PT's log window |

## 3. Save and start

1. Click **Save** / **OK** to close *Configure PT Script Modules…*.
2. Select `com.pawel.ptmcp` in the module list and click **Start**.
   If it was already started before you pasted the script, click **Stop** and
   then **Start** (or use **Run File** once) so the new script is evaluated.
3. Verify: PT's script log shows

   ```
   [pt-mcp] bridge ready (home=/home/pawel/.local/share/pt-mcp)
   ```

   and the directories now exist:

   ```fish
   ls -la ~/.local/share/pt-mcp
   # jobs  results  saved
   ```

## 4. Check it from the server side

```fish
cd /home/pawel/Code/Projects/packet-tracer-mcp
uv run pytest -q            # offline suite, no PT needed
```

Then, with the MCP server connected to your agent, call `pt_status` — it answers
`{"version": "9.0.1…", "deviceCount": N}` for the active file. That round trip
proves PT → bridge → result file → server all work.

## 5. Forcing a rescan (watcher not firing)

The bridge normally reacts to filesystem notifications on `jobs/`. Some setups
(AppImage/FUSE mounts, network homes, PT started from a different mount
namespace) deliver no events, and jobs then sit unanswered until the timeout.

Fix, in this order:

1. **Run File** — in **Extensions → Scripting**, click **Run File** and pick the
   module's `bridge.js`. Re-evaluating the script rescans `jobs/` immediately
   and re-registers the watcher.
2. **Restart the module** — Stop → Start in *Configure PT Script Modules…*.
3. **Call `ptmcp_rescan()`** from the PT Scripting console/interface.
4. Check that the `PT_MCP_HOME` path at the top of `bridge.js` is exactly the one
   the server uses (`echo $PT_MCP_HOME`, default
   `/home/pawel/.local/share/pt-mcp`). A mismatch means the two sides watch
   different directories and *every* call times out.

## 6. Updating the bridge later

`bridge/bridge.js` is the single source of truth, and PT holds a **copy** of it:

1. `git pull` in the repository.
2. Re-open *Configure PT Script Modules…* → module → **Script Engine** tab →
   select `bridge.js`.
3. Replace the editor contents with the new file, **Apply**, then **Stop** →
   **Start** the module.

## 7. Uninstalling

1. *Configure PT Script Modules…* → select `com.pawel.ptmcp` → **Stop**, then
   **Delete**.
2. Optionally remove the runtime directory: `rm -rf ~/.local/share/pt-mcp`.
   Nothing else on the system was touched — no service, no firewall rule, no
   network port.
