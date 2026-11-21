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
| — | calPad block in duplicate/mirror files (below) |

The last two are not cosmetic. FlashPrint writes a space after `layer_count:` and two
decimals on every `;layer:` marker, and the firmware's parser wants both — without them
the printer prints happily but shows no layer progress.

`M118` is the print's reach from the bed centre — max |X|, max |Y|, max Z — not its
width and depth; that is what FlashPrint writes (checked against a normal-mode file whose
model ran from X −40 to +128 and got `M118 X128.33`).

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
alone and OrcaSlicer asserts and dies mid-slice — still true as of 2.4.2. Both are set, currently to
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

## Priming

Two options, both in `generate.py`.

**Default — a bead on the plate.** Two passes along the front, X −50 → +50 so either head
can reach it, at Y −165 for the right head and Y −162 for the left. Both heads get one when
both are in use. Extrusion there is absolute (`G92 E0` … `G92 E0`), because Orca has not
emitted its `M83` yet at that point in the file.

**Purge on the wipe plates.** The Creator 4 has a plate past each end of the X axis that
the nozzles park on, so purging there and dragging the nozzle across is tidier — nothing
to peel off the build plate. Set `WIPE_PAD["enabled"] = True` and re-run `generate.py`:

```
M108 T0
G1 Z5.00 F420
G1 X205.00 Y0.00 F6000     ; onto the plate
G92 E0
G1 E20.0 F180              ; slow purge, so it coils rather than whips
G92 E0
G1 E-1 F1800
G1 X180.00 F3000           ; drag towards the middle
G1 X205.00 F3000           ; and back, twice
G1 X180.00 F3000
G1 X205.00 F3000
G1 X180.00 F6000
```

**The X coordinates in there are guesses and must be measured first.** The plates sit
outside the printable area, so these moves drive the head past X ±200 — get them wrong and
the carriage runs into the frame rather than stopping on the plate. Jog each head onto its
plate from the printer's move menu, read X off the display, and put those numbers in
`WIPE_PAD["right_x"]` and `["left_x"]`. `z` is the nozzle height above the plate while
purging, `scrape_mm` how far back towards the middle the drag goes, `passes` how many
times.

Worth checking first whether the firmware already does this by itself — the FlashPrint
reference file contains no purge or wipe sequence at all, which either means FlashPrint
leans on the raft, or the machine wipes on its own at print start.

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

## Duplicate and mirror

FlashPrint signals these three ways at once, and the presets and post-processor reproduce
all three, because there is no way to tell which one the firmware actually reads:

| | single | duplicate | mirror |
| --- | --- | --- | --- |
| `M118` suffix | `T0` | `T0 T1 D2` | `T0 T1 D1` |
| command after the start block | `M108 T0` | `M109 T2` | `M109 T1` |
| header flag at offset 40 | 15 | 31 | 47 |

`M653 S0` is identical in all three, so despite sitting in the start block it is not the
mode switch.

Pick `Flashforge Creator 4-X 0.4 nozzle DUPLICATE` or `… MIRROR` in the printer dropdown.
Those presets are single-extruder — one toolpath, both heads following it — but the start
G-code heats and waits for both nozzles, and the prime bead is laid *after* the mode
command so it is duplicated too and both nozzles get purged. There is no `M108` anywhere
in them, matching FlashPrint.

The build area narrows to 175 × 350, centred, which is FlashForge's published figure for
duplication on this machine rather than anything measured. If FlashPrint shows you a
different plate size in duplicate mode, change `half_x` in `IDEX_MODES` and re-run
`generate.py`.

### The calPad block

FlashPrint's duplicate and mirror files carry something normal-mode files do not: right
after `;layer_count:` and before the mode command, four "calibration pads", every move
commented out with `;:` so no slicer or preview shows them:

```
;calPad A start Z0.20
;support-start
;structure:cal-pad
;:G1 X18.49 Y-29.02 F6000
;:G1 X18.33 Y-25.83 E0.0977 F4800
...
;support-end
;calPad A end
;calPad B start Z0.20
...
;calPad SB end
M109 T2
```

The firmware executes those lines itself. Each pad covers the first layer's footprint plus
a margin: `A` and `B` are 30 % rasters (A's lines run along X, B's along Y), `SA` and `SB`
the solid versions, all at F4800 with absolute E from zero and the same E-per-mm as a
0.41 × layer-height line. A Creator 3 owner worked out what they are for: the two nozzles
are never at exactly the same height, and in duplicate mode both print at the same Z, so
the firmware lays a compensating raft under the copy printed by the higher nozzle and picks
the pad to do it with. Simplify3D users on the Creator 3 found the other effect the hard
way — with no calPad block, the firmware **does not place the two heads where it should**
(their copies overlapped; on the Creator 4 the left head ran into its end stop on the first
move and skipped). Pasting the block in fixed it.

`gx_post.py` therefore generates one for every duplicate/mirror file, from the first
layer's extrusion bounding box plus 3.2 mm, and inserts it in front of the mode command.
It makes no attempt to reproduce FlashPrint's rounded outline; the firmware only needs the
footprint and the four pad variants.

Status, 2026-09-16: files without the block crashed the left head; files with it have not
been printed yet. FlashPrint also prompts to add a raft in these modes — do that in the
process preset if the first layer of one copy misbehaves.

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

Verified by slicing through Orca 2.4.2's CLI and inspecting the result: single-extruder prints
across the 4-A/4-S/4-HT and 0.4/0.6/0.8 combinations slice cleanly; `M108` selects a head;
extrusion is absolute and resets every layer; no heater or fan command is left without a
tool index; the `.gx` header matches the FlashPrint reference field for field; the
PNG → BMP preview conversion is pixel-exact; the fallback time estimate lands within a few
percent of Orca's own.

**Not verified: dual-material output.** Orca's CLI crashes on every two-filament slice —
reproduced on 2.4.0-alpha and again on 2.4.2, with the stock Snapmaker J1 and Bambu H2D
profiles too, so it is an Orca bug rather than anything here, but it does mean the two-head path has only
been exercised against hand-written G-code. Before a long dual print: run the printer's
nozzle offset calibration (Orca sends both heads the same coordinates and lets the
firmware apply the offset, exactly as FlashPrint does), print something small in two
colours, and check with `--info` that both filament figures are non-zero. The prime tower
sits at the front left. The idle head is not dropped to a standby temperature, so it may
ooze.

`M653`, `M654` and `M656` are copied from the reference file with the same values
FlashPrint used; what they do is not documented anywhere I could find.
