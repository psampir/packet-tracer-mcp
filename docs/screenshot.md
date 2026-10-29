# Screenshots: getting the topology out of Packet Tracer

A lab report needs the diagram, and Packet Tracer 9.0.1 has **no API to render the
workspace to an image**: `ipc.appWindow()` exposes the window and the file
operations, `LogicalWorkspace` exposes devices, links and the CLI, but nothing
returns a bitmap. `packet-tracer-mcp` therefore has no `export_png`: PTM-13 ends as
"wontfix + docs", and this page is the docs.

Two ways out, both fine:

| Path | Use it when |
|---|---|
| **A. niri + grim** (below) | you are on Linux/Wayland and want a file on disk |
| **B. manual screenshot** | any OS, or when A is not available |

Neither needs Packet Tracer to be restarted, and neither touches the `.pkt`.

## A. Linux / Wayland (niri + grim)

Prerequisites, all in Fedora's repos:

```fish
sudo dnf install -y grim slurp ImageMagick jq
```

`grim` is a Wayland screenshot tool; under niri it captures an **output** (the
whole monitor), not a single window: that is the trap this recipe avoids.

### 1. Find the Packet Tracer window

```fish
niri msg -j windows | jq -r '.[] | select(.app_id=="PacketTracer") | "\(.id)\t\(.title)"'
# 136	Cisco Packet Tracer
# 143	Log
# 142	Configure PT Script Modules
```

Take the id of the main window (`Cisco Packet Tracer`, or the lab's `.pkt`
title). If PT is on another workspace, this still works: niri switches to it.

### 2. Focus it

```fish
niri msg action focus-window --id 136
```

### 3. Grab the window: fullscreen trick (automatic)

niri can make a window fill the output, which turns "capture the window" into
"capture one output":

```fish
set -l out (niri msg -j focused-output | jq -r '.name')   # e.g. eDP-1
niri msg action fullscreen-window
sleep 0.3                                                 # let it settle
grim -o $out ~/pt-shots/pt-window.png
niri msg action fullscreen-window                         # toggle back
```

`grim -o $out` writes the output at its native resolution. On a HiDPI laptop you
may want `grim -s 2 -o $out …` (scale 2) to keep the PNG small; check
`niri msg -j outputs | jq '.[] | {name, scale}'` for the current scale.

### 4. Or select the window by hand (interactive)

```fish
grim -g "$(slurp)" ~/pt-shots/pt-window.png
```

`slurp` freezes the screen and prints `X,Y WxH` for the box you drag; wrap the
Packet Tracer window with it. This is the most reliable option when PT shares
the screen with other windows and you do not want to fullscreen it.

### 5. Or crop a full-screen grab afterwards

If you already have `grim ~/pt-shots/full.png`, crop it with ImageMagick:

```fish
magick ~/pt-shots/full.png -crop 1091x1192+300+120 +repage ~/pt-shots/pt-window.png
```

or with Pillow, when you want the coordinates in a script:

```python
from PIL import Image

shot = Image.open("shot-full.png")
window = shot.crop((300, 120, 300 + 1091, 120 + 1192))
window.save("shot-pt-window.png")
```

For a quick "PT is the only window on a flat background" grab, `magick full.png
-trim +repage pt-window.png` removes the uniform border, but it also trims the
background *inside* the topology pane, so check the result before using it.

### 6. Check the result

```fish
file ~/pt-shots/pt-window.png     # PNG image data, 1091 x 1192, 8-bit/color RGBA
```

Then look at it before putting it in a report: the interesting part is usually
the topology area plus one or two `show` windows, not the whole canvas.

## B. Manual screenshot (any OS)

1. Put the topology and the CLI windows you want side by side in PT.
2. Take the screenshot with the system tool: **Print Screen** on Windows,
   **Cmd+Shift+4** on macOS, `grim -g "$(slurp)"` / `niri msg action screenshot`
   on niri (opens the screenshot UI, which can save a file or copy to clipboard).
3. Crop to the relevant area: the PT canvas is mostly white space.

Agents without a browser/desktop can still produce a diagram: render it from the
topology the bridge already knows (`list_devices` + `list_links`) with
Graphviz or matplotlib, or as a small HTML/SVG page and call the client's
`browser.preview`. That is a *diagram of the topology*, not a screenshot; say so
in the report.

## What to capture for a lab report

| Shot | How |
|---|---|
| Topology | the canvas with device names and link labels visible (PT: **Options → Preferences → Show link labels**) |
| Addressing | `get_config("PC-Users")` / `list_ports("R1")` output, or `show ip interface brief` |
| Routing / VLANs | `show("R1", "show ip route")`, `show("S1", "show vlan brief")` |
| Services | `dns_list("Server-DNS")`, `http_page("Server-DNS")` |
| Proof of connectivity | `ping("PC-Users", "192.168.20.10")`; its `output` is the citable evidence |

Text output is better evidence than a screenshot for configuration; use the PNG
for the diagram.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `grim` writes a PNG of the whole desktop | Expected: grim captures outputs, not windows. Use the fullscreen trick (step 3) or `slurp` (step 4). |
| `grim: no output matched` | The output name from `niri msg -j focused-output` changed (hot-plug); re-read it. |
| The PNG is huge / blurry | HiDPI scale is 2 or more. `grim -s 2` halves the pixels; check with `niri msg -j outputs`. |
| `niri msg action focus-window --id N` fails | The window closed; re-list windows. Ids are per-session and change on restart. |
| PT's **File → Save As** only offers `.pkt` | Correct: 9.0.1 cannot export an image. Use the desktop screenshot. |
