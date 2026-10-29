# Packet Tracer setup (click-by-click)

One-time work in the Packet Tracer GUI. Afterwards PT exposes the file-drop bridge
on its own every time it starts: nothing here has to be repeated per session.

> **Version note.** The labels below were verified against **Cisco Packet Tracer
> 9.0.1** on Fedora 44. Packet Tracer's Scripting UI is stable but not frozen:
> if a label differs slightly in your build, the tab order is the same.

## Quick install (bundled module)

Download `packet-tracer-mcp.pts` from the
[latest release](https://github.com/psampir/packet-tracer-mcp/releases/latest).
Importing it is the whole setup; name, ID, script and requested privileges travel
with the file.

1. Start Packet Tracer and open the lab you want to drive.
2. **Extensions → Scripting → Configure PT Script Modules… → Add…**
3. Select the downloaded `packet-tracer-mcp.pts` and click **Open**.
4. Select `packet-tracer-mcp` in the list and click **Start**.
5. Quit once with **File → Exit**, so PT saves the registration (see the clean-exit
   note in §3).

That is it. The numbered sections below are the manual **build-from-source** flow,
needed only when you edit `bridge/bridge.js` or your PT build refuses the bundled
file.

## 0. Before you start

- Packet Tracer **9.0.1+** installed and licensed (this project uses only the
  documented Scripting/Extensions API).
- The MCP server installed globally:

  ```sh
  uv tool install "git+https://github.com/psampir/packet-tracer-mcp.git@main"
  ```

  This puts `packet-tracer-mcp` on `PATH`; update it with the same command plus
  `--force`. For development, a checkout with `uv run --directory …` works too.
- For the manual flow below: [`bridge/bridge.js`](../bridge/bridge.js) open in
  an editor; you will paste its **entire** contents into PT. The bundled module
  skips this.

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
| **ID** | `com.psampir.packet-tracer-mcp` |
| **Name** | `packet-tracer-mcp` |
| **Startup** | **On Startup**: so the bridge comes up with PT, no clicking later |

### Script Engine tab

1. Click **Add** (or the **+** button) to add a script file to the module.
2. Name the file **`bridge.js`**: the name is cosmetic, but keeping it identical
   to the repository copy avoids confusion when you update it.
3. Paste the **whole** of `bridge/bridge.js` into the editor pane (it is ES5 /
   QtScript: `var` and `function` only; do not "modernise" it).
4. Click **Apply** / **OK** to close the editor.

> The 0.2.0 bridge adds the per-device mode/readiness logic (`get_state`, `plan`,
> `configure`, `exec`), so **the old copy in PT must be replaced**: see
> [§7](#7-updating-the-bridge-later). The rules it implements are in
> [cli-semantics.md](cli-semantics.md).

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
2. Select `com.psampir.packet-tracer-mcp` in the module list and click **Start**.
   If it was already started before you pasted the script, click **Stop** and
   then **Start** (or use **Run File** once) so the new script is evaluated.
3. Verify: PT's script log shows

   ```
   [pt-mcp] bridge ready (home=/home/you/pt/pt-mcp)
   ```

   and the directories now exist:

   ```fish
   ls -la ~/pt/pt-mcp
   # jobs  results  saved
   ```

   On Windows and macOS the directory sits under Packet Tracer's own user
   folder. You never have to guess it: `packet-tracer-mcp --doctor` prints the
   path it resolved, the candidates it tried, and the last answer the bridge
   gave. Exit code 0 means a live mailbox was found.

> **Quit Packet Tracer cleanly.** The module registration lives in PT's profile
> and is written on **`File → Exit`**. A crash or a killed process loses it: the
> next launch starts with an empty module list, so nothing auto-starts. If that
> happens, re-add `packet-tracer-mcp.pts` and quit cleanly once; after that it
> starts with PT.

## 4. Check it from the server side

```fish
cd /home/pawel/Code/Projects/packet-tracer-mcp
uv run pytest -q            # offline suite, no PT needed
```

Then, with the MCP server connected to your agent, call `status`; it answers
`{"version": "9.0.1…", "deviceCount": N}` for the active file. That round trip
proves PT → bridge → result file → server all work.

## 5. Forcing a rescan (watcher not firing)

The bridge normally reacts to filesystem notifications on `jobs/`. Some setups
(AppImage/FUSE mounts, network homes, PT started from a different mount
namespace) deliver no events, and jobs then sit unanswered until the timeout.

Fix, in this order:

1. **Run File**: in **Extensions → Scripting**, click **Run File** and pick the
   module's `bridge.js`. Re-evaluating the script rescans `jobs/` immediately
   and re-registers the watcher.
2. **Restart the module**: Stop → Start in *Configure PT Script Modules…*.
3. **Call `ptmcp_rescan()`** from the PT Scripting console/interface.
4. Check that the `PT_MCP_HOME` path at the top of `bridge.js` is exactly the one
   the server uses (`echo $PT_MCP_HOME`, default `~/pt/pt-mcp`). The bridge builds
   it from PT's own user folder (`getUserFolder() + "/pt-mcp"`), so the two only
   diverge when `PT_MCP_HOME` is set on one side and not the other. A mismatch
   means the two sides watch different directories and *every* call times out.

## 6. Recovery: a wedged console

A console can stop accepting input without Packet Tracer crashing: the prompt
never comes back after a command, two calls arrive out of order, or a mistyped
command starts a DNS lookup that blocks the line. The bridge has no wall-clock
timer, so it cannot break such a state on its own, the way out is one of the
actions below, **in this order**. Each step is cheaper and less destructive than
the next one.

1. **Wait for the prompt.** Most "hangs" are a slow device. Give it the full
   `ready_timeout` (default 45 s) and retry the call once; a 2911 in its
   initial-configuration dialog, a switch still booting and a busy `ping` all
   look identical from the outside.
   ```jsonc
   show("S1", "show vlan brief", ready_timeout=60)
   ```
2. **Power-cycle the device**: off, then on and wait:
   ```jsonc
   power("S1", on=False)
   power("S1", on=True, ready=True, ready_timeout=90)
   ```
   `ready=True` polls `get_state` until a real prompt appears, so the next call
   is not typed into a boot sequence.
3. **Skip the boot** if the device sits in its boot/dialog phase instead of
   power-cycling it again:
   ```jsonc
   skip_boot("S1")                    # or skip_boot("S1", wait=True)
   ```
4. **Restart Packet Tracer itself** (close the app, reopen the `.pkt`, make sure
   the Script Module is *Started*) when the console is fine but *no* op answers
  , that is a bridge/watcher problem, not a device problem. Check
   [§5](#5-forcing-a-rescan-watcher-not-firing) first: **Run File** is usually
   enough.
5. **Re-paste `bridge.js`** only if the module answers but behaves like an older
   version, see [§7](#7-updating-the-bridge-later).

> **A power-cycle discards everything that is not saved in the `.pkt`.** The
> running config of that device (interfaces, VLANs, routing, DHCP pools, DNS
> entries, pages) is gone after step 2, `save` first if you have anything to
> keep, and re-apply the device's configuration afterwards. Per-device config is
> not part of the bridge's state; there is no undo.

Power-cycling does **not** fix: a wrong device name (`device not found`), a
missing `DnsServer`/`HttpServer` process (only some device types run them), a
bridge that never started, or a `PT_MCP_HOME` mismatch between PT and the server.
Those are configuration problems, the error message says which one.

## 7. Updating the bridge later

`bridge/bridge.js` is the single source of truth, and PT holds a **copy** of it:

1. `git pull` in the repository (`bridge.js` changes are called out in the
   [CHANGELOG](../CHANGELOG.md); 0.2.0 changed mode/readiness handling).
2. Re-open *Configure PT Script Modules…* → module → **Script Engine** tab →
   select `bridge.js`.
3. **Replace the editor contents** with the whole new file, click **Apply**.
4. **Stop** the module, then **Start** it. The module keeps running the old
   script until you do this, *Run File* only re-evaluates it, which is enough
   for a rescan but not a substitute for Stop → Start.
5. If the module does not come back, or the first call still behaves like the old
   script, re-paste and Stop → Start again; a half-replaced file is the usual
   cause.
6. **Quit with `File → Exit`** after the change. A crash or `kill` loses the
   module registration (see the note in §3).

The CLI/mode rules the new file implements are documented in
[cli-semantics.md](cli-semantics.md), worth a skim before the first
`configure` after an update.

### Renaming the module (0.3.0)

0.3.0 drops the `pt`/`pt-mcp` shorthand from the public surface, so the module
registered in PT should be renamed by hand once. This is **GUI work only**:
nothing in the repository touches your PT profile or `~/pt/extensions`.

1. *Configure PT Script Modules…* → select the old module → **General** tab.
2. Set **Name:** `packet-tracer-mcp` and
   **ID:** `com.psampir.packet-tracer-mcp`, then **Save**.
3. **Quit with `File → Exit`**: the clean-exit rule from §3. The new name/ID
   only persists on a clean exit.
4. Start PT again and confirm the renamed module is listed and started.
5. Only **after** PT has re-saved the renamed module, delete the old
   `ptmcp.pts` / `ptmcp` files from `~/pt/extensions`, they are stale copies
   from the pre-0.3.0 name and nothing reads them any more.

The runtime directory is `~/pt/pt-mcp`: the bridge derives it from PT's user
folder, so a module exported on another machine finds it without an edit. Set
`PT_MCP_HOME` on both sides to point elsewhere (`~/.local/share/pt-mcp` from
pre-0.3.2 releases still works that way).

## 8. Uninstalling

1. *Configure PT Script Modules…* → select `com.psampir.packet-tracer-mcp` → **Stop**, then
   **Delete**.
2. Optionally remove the runtime directory: `rm -rf ~/pt/pt-mcp`.
   Nothing else on the system was touched, no service, no firewall rule, no
   network port.

## 9. Rebuilding the release asset

`packet-tracer-mcp.pts` is exported from PT; CI cannot generate it. Rebuild it
whenever `bridge/bridge.js` changes:

1. *Configure PT Script Modules…* → `packet-tracer-mcp` → **Edit**.
2. **Script Engine** → replace `bridge.js` with the new file → **Apply**.
3. **Stop → Start**.
4. **File → Export** and save the module as `packet-tracer-mcp.pts`.
5. Attach it to the release:
   `gh release upload vX.Y.Z packet-tracer-mcp.pts --clobber`.

The `.pts` is encrypted and tied to the PT build. Bump the project version when the
asset changes, so the release notes, the wheel and the module stay in step.

## 10. Windows and macOS

There is nothing platform-specific to configure, because the module derives the
mailbox from Packet Tracer instead of a fixed path: it is `getUserFolder() +
"/pt-mcp"` inside PT. The server finds that directory by trying, in order:

1. `~/pt/pt-mcp` (Packet Tracer 9.0.1 on Linux keeps its profile in `~/pt`)
2. `~/Documents/pt-mcp`
3. `~/Documents/Packet Tracer*/pt-mcp`
4. `~/Library/Application Support/Packet Tracer*/pt-mcp`

The first candidate that already contains `jobs/` and `results/` wins, because
those directories exist only after the Script Module has started. `PT_MCP_HOME`
overrides the search, which is the escape hatch when your PT profile lives
somewhere unusual (a redirected Documents folder, a network home).

Diagnosis without guesswork:

```sh
packet-tracer-mcp --doctor
```

It never contacts Packet Tracer, so run it with the module stopped too. If it
reports no mailbox at all, start the module once and run it again; if PT's log
shows `[pt-mcp] bridge ready (home=…)` with a path that is not in the candidate
list, set `PT_MCP_HOME` to it on both sides (server and module) and restart the
client.
