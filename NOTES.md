# Notes

Background for the [FlashForge Creator 4 profiles](README.md).

## What the post-processor changes

Orca speaks Marlin; the Creator 4 speaks FlashForge. `gx_post.py` translates:

| Orca | Creator 4 |
| --- | --- |
| `T0` / `T1` | `M108 T0` / `M108 T1` |
| `M109 S210 T1` | `M104 S210 T1` + `M6 T1` |
| `M190 S60` | `M140 S60 T0` + `M7 T0` |
| `M104` / `M140` / `M106` / `M107` | same, with the tool index appended |
| relative extrusion (`M83`) | absolute, with `G92 E0` every layer |
| `M73`, `G21` | dropped |
| `M84` | `M18` |
| `Z.2` | `Z0.2` |
| — | `M118 X.. Y.. Z.. T0` bounding box inserted |
| `;layer_count:11` | `;layer_count: 11` |
| `;layer:0.2` | `;layer:0.20` |

The last two are not cosmetic. FlashPrint writes a space after `layer_count:` and two
decimals on every `;layer:` marker, and the firmware's parser wants both — without them
the printer prints happily but shows no layer progress.

Then it builds the container: 58-byte header, 80 × 60 BMP preview at offset 58, 320 × 320
PNG preview at 14512, G-code after that. Orca's own thumbnail becomes the PNG and is
downscaled for the BMP. Print time, filament used, layer height, shell count, speed and
temperatures go in the header; if Orca's estimate is missing the script measures the time
off the toolpaths rather than writing a zero, which is what makes the printer display
`0:00:00`.

## Two things that will bite you

**Orca emits no tool-select command when only one extruder is in use.** Print on the left
head and the machine happily keeps driving the right one — motion, no plastic. The start
G-code therefore ends with `M108 T{initial_extruder}`, and the post-processor inserts one
if it ever sees a file that extrudes without picking a head first.

**`extruder_printable_area` needs `extruder_printable_height` next to it.** Set the area
alone and OrcaSlicer 2.4.0-alpha asserts and dies mid-slice. Both are set, currently to
the full plate for both heads:

```json
"extruder_printable_area": ["-200x-175,200x-175,200x175,-200x175",
                            "-200x-175,200x-175,200x175,-200x175"],
"extruder_printable_height": ["500", "500"]
```

First string is the right head, second the left, four `XxY` corners each. The bed origin
is the **centre** of the plate, as on the machine itself, so X runs −200 → 200 and Y
−175 → 175. To model a head that cannot reach the far side, narrow its polygon — jog each
head to its limits from the printer's panel and read the coordinates off the display.

## Prime line

The start G-code lays a two-pass prime bead along the front of the plate before the print
starts — X −50 → +50, i.e. centred, so either head can reach it, at Y −165 for the right
head and Y −162 for the left. Both heads get one when both are in use. Extrusion there is
absolute (`G92 E0` … `G92 E0`), because Orca has not emitted its `M83` yet at that point
in the file.

To move or resize it, edit `START_GCODE` in `generate.py`. Remember the origin is the
middle of the plate.

## Dual material

In Orca: add a second filament slot with the **+** in the filament bar, then assign work to
it — right-click an object → *Change filament* → 2, per-part assignment for multi-part
models, or the colour painting tool for regions of a single mesh. Soluble or contrast
supports go on the second head via Process → Support → *Support filament* and *Support
interface filament*. Filament 1 is the right nozzle, filament 2 the left.

Purge volumes are set for a real dual-nozzle machine rather than a single-nozzle
multi-material one. Orca's default flush of ~84 mm³ per tool change assumes it has to push
one colour out through the other nozzle; with two nozzles the tower only has to recover
from ooze while a head sat idle, so `flush_multiplier` is 0.05 (~14 mm³) and purge material
goes into infill where it can. If the first dual print shows stringing or colour carry-over
on the object, raise it; if the tower is enormous, it went the wrong way.

The prime tower is 60 mm wide at the front left (`wipe_tower_x/y` = −180 / −160). Keep
that corner clear.

## File formats

The Creator 4 accepts plain text G-code as well as `.gx`, so the container is optional —
what it is *not* optional about is the dialect. Run the post-processor with `--plain` to
get readable G-code and set the process preset's filename format to `.gcode` or `.g`:

```bash
python3 gx_post.py --plain plate_1.gcode
```

The trade-off is the display: without the container the printer has no preview, no time
estimate and no layer count, since all three live in the `.gx` header.

## Machine details

Bed 400 × 350 × 500 mm, origin centred. Start and end G-code are FlashPrint's, with Orca
placeholders for the temperatures: `M140`/`M104` then `M7`/`M6` waits, `M654 P60`,
`M653 S0`, `M656 S0`, `M651 S255` for the side fan, and `G162 Z` / `M652` / `M18` to
finish. There is no `G28` — the Creator 4 homes itself. The side fan is switched on for
PLA, PETG and PVA and off for everything else. Machine limits stay out of the G-code
(`emit_machine_limits_to_gcode` is off), so no `M201`/`M203`/`M205` reach the firmware.

Process speeds are capped by volumetric flow — 12 mm³/s on Standard, 16 on Fast — so the
0.8 nozzle profiles do not ask for more plastic per second than the hot end can melt, and
by a 120 mm/s ceiling, because the gantry carries two heads.

## What is verified, and what is not

Verified by slicing through Orca's CLI and inspecting the result: single-extruder prints
across the 4-A/4-S/4-HT and 0.4/0.6/0.8 combinations slice cleanly; `M108` selects a head;
extrusion is absolute and resets every layer; no heater or fan command is left without a
tool index; the `.gx` header matches the FlashPrint reference field for field; the
PNG → BMP preview conversion is pixel-exact; the fallback time estimate lands within a few
percent of Orca's own.

**Not verified: dual-material output.** Orca 2.4.0-alpha's CLI crashes on every
two-filament slice — reproduced with the stock Snapmaker J1 and Bambu H2D profiles too, so
it is an Orca bug rather than anything here, but it does mean the two-head path has only
been exercised against hand-written G-code. Before a long dual print: run the printer's
nozzle offset calibration (Orca sends both heads the same coordinates and lets the
firmware apply the offset, exactly as FlashPrint does), print something small in two
colours, and check with `--info` that both filament figures are non-zero. The prime tower
sits at the front left. The idle head is not dropped to a standby temperature, so it may
ooze.

`M653`, `M654` and `M656` are copied from the reference file with the same values
FlashPrint used; what they do is not documented anywhere I could find.
