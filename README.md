# OrcaSlicer profiles for the FlashForge Creator 4

Orca has no Creator 4 profile, and the Creator 4 does not speak plain Marlin G-code. This
adds the presets, plus a post-processing script that rewrites Orca's output into the
FlashForge dialect and wraps it in a `.gx` container like FlashPrint's. Tested against
OrcaSlicer 2.4.2.

* **Printers** — 4-A, 4-S, 4-HT × 0.4 / 0.6 / 0.8 nozzle
* **Processes** — three layer heights × Standard/Fast per nozzle, flow-capped
* **Filaments** — PLA, PETG, ABS, ASA, TPU, plus PC and PA-CF on the 4-S / 4-HT

## Install

Close OrcaSlicer first, then:

```bash
python3 install.py
```

Presets land in every Orca install it finds (native, Flatpak, Snap, macOS, Windows), the
post-processing script gets wired up, and the parent printer model is registered.
`--uninstall` reverses it. By hand instead: import `dist/FlashForge Creator 4.orca_printer`
via **File → Import → Import Configs** and set the post-processing script to
`python3 /path/to/gx_post.py` yourself.

Pick your machine in Orca under **Custom Printer → Generic Marlin Printer**.

## Using it

Extruder 1 = T0 = **right** head, extruder 2 = T1 = **left**; assign an object to filament
2 to print it on the left. A prime bead is laid along the front edge before each print.
Export writes a ready-to-copy `.gx`. To inspect one:

```bash
python3 gx_post.py --info yourfile.gx     # previews, time, filament, temps
python3 gx_post.py --dump yourfile.gx     # the G-code back out
```

Duplicate and mirror printing have their own presets — `… 0.4 nozzle DUPLICATE` and
`… MIRROR` — with the build area narrowed to one copy's worth and both nozzles driven from
one toolpath. The post-processor adds the "calPad" block the firmware needs to position the
two heads in these modes (see NOTES.md); a raft is recommended, as FlashPrint suggests.

The printer takes plain G-code too — `gx_post.py --plain` skips the container, at the cost
of the preview, time and layer count on the printer's screen.

Dual-material is set up but untested — Orca's CLI cannot slice two filaments at all
(reproduced on 2.4.0-alpha and 2.4.2, stock profiles included), so that path has only
been checked against hand-written G-code.

`presets/` is generated; edit `generate.py` and re-run it. [NOTES.md](NOTES.md) covers the
G-code translation, the `.gx` layout, per-nozzle X limits, and what is and isn't verified.

## Licence

MIT. Not affiliated with FlashForge or the OrcaSlicer project.
