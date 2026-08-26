#!/usr/bin/env python3
"""
Generates the OrcaSlicer preset matrix for the FlashForge Creator 4.

Everything under presets/ is generated - edit this file, not the JSON.

    python3 generate.py

Matrix:
  printers  3 hotends (4-A / 4-S / 4-HT) x 3 nozzles (0.4 / 0.6 / 0.8)
  processes 3 layer heights x 2 speed tiers per nozzle
  filaments per hotend class, tuned for the Creator 4's chamber and side fan
"""

import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "presets")

# The post-processing command is patched by install.py; this placeholder is
# what ships in the repo.
GX_POST = "__GX_POST_COMMAND__"

# layer heights offered per nozzle, coarsest last
LAYERS = {0.4: [0.12, 0.18, 0.24], 0.6: [0.18, 0.28, 0.36], 0.8: [0.24, 0.36, 0.48]}


# --------------------------------------------------------------------------
# priming
# --------------------------------------------------------------------------
# The Creator 4 has a wipe plate past each end of the X axis - the right head
# parks on the right one, the left head on the left. Purging onto those and
# dragging the nozzle across them is cleaner than a bead on the plate, but it
# means driving the head OUTSIDE the printable area, so the coordinates have to
# be right. Measure them on the machine (jog each head onto its plate from the
# printer's own move menu and read X off the display) before switching this on.
#
# Until then the fallback is a two-pass bead along the front of the plate.
WIPE_PAD = {
    "enabled": False,
    "right_x": 205.0,        # UNVERIFIED - measure before enabling
    "left_x": -205.0,        # UNVERIFIED - measure before enabling
    "y": 0.0,                # where along the plate to sit
    "z": 5.0,                # nozzle height over the plate while purging
    "purge_mm": 20.0,        # filament extruded onto the plate
    "scrape_mm": 25.0,       # how far to drag back towards the middle
    "passes": 2,
}


def prime_block(extruder):
    """Prime sequence for one head, as a list of G-code lines."""
    tool = "T%d" % extruder
    if not WIPE_PAD["enabled"]:
        # bead along the front of the plate, centred in X so either head reaches it
        y = -165.0 + extruder * 3.0
        return [
            "M108 " + tool,
            "G1 Z{initial_layer_print_height} F420",
            "G1 X-40 Y%.1f F6000" % y,
            "G92 E0",
            "G1 X40 Y%.1f E8 F1200" % y,
            "G1 X40 Y%.1f F6000" % (y + 0.8),
            "G1 X-40 Y%.1f E16 F1200" % (y + 0.8),
            "G92 E0",
            "G1 E-1 F1800",
            "G1 Z2 F420",
            "G1 X50 Y%.1f F6000" % (y + 0.8),
        ]

    pad = WIPE_PAD["right_x"] if extruder == 0 else WIPE_PAD["left_x"]
    inward = -1.0 if extruder == 0 else 1.0      # towards the middle of the bed
    scrape = WIPE_PAD["scrape_mm"] * inward
    lines = [
        "M108 " + tool,
        "G1 Z%.2f F420" % WIPE_PAD["z"],
        "G1 X%.2f Y%.2f F6000" % (pad, WIPE_PAD["y"]),
        "G92 E0",
        "G1 E%.1f F180" % WIPE_PAD["purge_mm"],   # slow, so it coils on the plate
        "G92 E0",
        "G1 E-1 F1800",
    ]
    for _ in range(WIPE_PAD["passes"]):
        lines.append("G1 X%.2f F3000" % (pad + scrape))   # drag across the plate
        lines.append("G1 X%.2f F3000" % pad)
    lines.append("G1 X%.2f F6000" % (pad + scrape))
    return lines

# --------------------------------------------------------------------------
# printers
# --------------------------------------------------------------------------
# Nozzle temperature ceilings are the published figures for each extruder
# option. They are enforced on the filament side, which is where Orca keeps
# them; the printer preset carries the nozzle hardness and the notes.
HOTENDS = {
    "4-A": dict(max_temp=265, nozzle_type="brass", hrc=0,
                blurb="general purpose extruder, up to 265 C"),
    "4-S": dict(max_temp=320, nozzle_type="hardened_steel", hrc=60,
                blurb="engineering extruder, up to 320 C"),
    "4-HT": dict(max_temp=360, nozzle_type="hardened_steel", hrc=60,
                 blurb="high temperature extruder, up to 360 C"),
}
NOZZLES = [0.4, 0.6, 0.8]

# Duplicate and mirror, as FlashPrint drives them: the command goes after the
# start block, both heads are heated, and one toolpath feeds both. The window
# is the per-copy build area from FlashForge's spec, 175 x 350.
IDEX_MODES = {
    "": None,
    "DUPLICATE": dict(command="M109 T2", half_x=87.5),
    "MIRROR": dict(command="M109 T1", half_x=87.5),
}

BED = ["-200x-175", "200x-175", "200x175", "-200x175"]
BED_FLAT = "-200x-175,200x-175,200x175,-200x175"

def start_gcode(mode=""):
    """Start block, straight out of the FlashPrint reference file."""
    heat = [
        "{if is_extruder_used[0]}M104 S{nozzle_temperature_initial_layer[0]} T0"
        "{else}M104 S0 T0{endif}",
        "{if is_extruder_used[1]}M104 S{nozzle_temperature_initial_layer[1]} T1"
        "{else}M104 S0 T1{endif}",
    ]
    wait = [
        "{if is_extruder_used[0]}M6 T0{endif}",
        "{if is_extruder_used[1]}M6 T1{endif}",
    ]
    if mode:
        # one filament, but both nozzles print, so both get heated and waited on
        heat = ["M104 S{nozzle_temperature_initial_layer[0]} T0",
                "M104 S{nozzle_temperature_initial_layer[0]} T1"]
        wait = ["M6 T0", "M6 T1"]

    lines = [";start gcode", ";machine_type: Creator 4",
             "M140 S{bed_temperature_initial_layer_single} T0"] + heat + [
        "M654 P60", "M653 S0", "M656 S0", "M107 T0", "M107 T1",
        "M204 S1000", "M900 K0.000 T0", "M900 K0.000 T1",
        "G90", "G1 Z50.000 F420", "M7 T0"] + wait + [
        '{if filament_type[initial_extruder]=="PLA" or filament_type[initial_extruder]=="PETG"'
        ' or filament_type[initial_extruder]=="PVA"}M651 S255{else}M652{endif}',
    ]

    if mode:
        # mode first, then prime - so the prime is duplicated/mirrored too and
        # both nozzles get purged. No M108 anywhere: FlashPrint emits none.
        lines.append(";layer_count: {total_layer_count}")
        lines.append(IDEX_MODES[mode]["command"])
        lines += [l for l in prime_block(0) if not l.startswith("M108")]
    else:
        lines.append("{if is_extruder_used[0]}" + "\n".join(prime_block(0)) + "{endif}")
        lines.append("{if is_extruder_used[1]}" + "\n".join(prime_block(1)) + "{endif}")
        lines.append(";layer_count: {total_layer_count}")
        # Orca emits no tool select when only one extruder is in use, so the head
        # has to be named here or the printer keeps driving whichever was last active
        lines.append("M108 T{initial_extruder}")
    return "\n".join(lines)


END_GCODE = "\n".join([
    ";end gcode", "M107 T0", "M107 T1", "M104 S0 T0", "M104 S0 T1",
    "M140 S0 T0", "G162 Z F1800", "M652", "G91", "M18",
])


def machine_name(hotend, nozzle, mode=""):
    name = "Flashforge Creator %s %s nozzle" % (hotend, nozzle)
    return (name + " " + mode).strip()


def machine(hotend, nozzle, mode=""):
    spec = HOTENDS[hotend]
    d = nozzle
    m = single_head(idex_machine(hotend, nozzle, mode), mode) if mode \
        else idex_machine(hotend, nozzle, mode)
    return m


def single_head(d, mode):
    """Duplicate and mirror drive both heads from one toolpath, so the preset
    is a single-extruder printer that happens to heat two nozzles."""
    for k, v in list(d.items()):
        if isinstance(v, list) and len(v) == 2 and k != "printable_area":
            d[k] = [v[0]]
    d["nozzle_diameter"] = [d["nozzle_diameter"][0]]
    d["printer_extruder_id"] = ["1"]
    # only meaningful with two extruders, and area-without-height kills Orca
    d.pop("extruder_printable_area", None)
    d.pop("extruder_printable_height", None)
    half = IDEX_MODES[mode]["half_x"]
    d["printable_area"] = ["-%sx-175" % half, "%sx-175" % half,
                           "%sx175" % half, "-%sx175" % half]
    return d


def idex_machine(hotend, nozzle, mode=""):
    spec = HOTENDS[hotend]
    d = nozzle
    return {
        "type": "machine",
        "name": machine_name(hotend, nozzle, mode),
        "from": "User",
        "inherits": "MyMarlin 0.4 nozzle",
        "instantiation": "true",
        "is_custom_defined": "0",
        "printer_settings_id": machine_name(hotend, nozzle, mode),
        "printer_variant": str(nozzle),
        "printer_technology": "FFF",
        "printer_structure": "i3",
        "version": "2.4.0.0",

        "nozzle_diameter": [str(d), str(d)],
        "extruder_offset": ["0x0", "0x0"],
        "extruder_colour": ["#FF6600", "#00A0E9"],
        "single_extruder_multi_material": "0",
        "nozzle_type": spec["nozzle_type"],
        "nozzle_hrc": str(spec["hrc"]),
        "nozzle_volume": "0",

        "extruder_type": ["Direct Drive", "Direct Drive"],
        "extruder_variant_list": ["Direct Drive Standard", "Direct Drive Standard"],
        "printer_extruder_variant": ["Direct Drive Standard", "Direct Drive Standard"],
        "printer_extruder_id": ["1", "2"],
        "master_extruder_id": "1",
        # both of these, always - setting the area without the height makes
        # Orca 2.4.0-alpha assert and die mid-slice
        "extruder_printable_area": [BED_FLAT, BED_FLAT],
        "extruder_printable_height": ["500", "500"],

        "printable_area": BED,
        "printable_height": "500",
        "bed_exclude_area": ["0x0"],
        "best_object_pos": "0.5,0.5",

        "gcode_flavor": "marlin",
        "use_relative_e_distances": "1",
        "use_firmware_retraction": "0",
        "emit_machine_limits_to_gcode": "0",
        "remaining_times": "0",
        "disable_m73": "1",
        "silent_mode": "0",
        "auxiliary_fan": "0",
        "support_air_filtration": "0",
        "support_chamber_temp_control": "0",
        "support_multi_bed_types": "0",
        "scan_first_layer": "0",
        "manual_filament_change": "0",
        "purge_in_prime_tower": "1",
        "enable_filament_ramming": "0",
        "machine_load_filament_time": "0",
        "machine_unload_filament_time": "0",
        "machine_tool_change_time": "10",

        "thumbnails": ["320x320"],
        "thumbnails_format": "PNG",

        "default_filament_profile": ["Flashforge Creator 4 PLA"],
        "default_print_profile": process_name(LAYERS[d][1], "Standard", d),

        "max_layer_height": [str(round(d * 0.8, 2))] * 2,
        "min_layer_height": [str(round(d * 0.2, 2))] * 2,

        "retraction_length": ["1", "1"],
        "retraction_speed": ["30", "30"],
        "deretraction_speed": ["30", "30"],
        "retraction_minimum_travel": ["1.5", "1.5"],
        "retract_when_changing_layer": ["1", "1"],
        "retract_before_wipe": ["0%", "0%"],
        "retract_restart_extra": ["0", "0"],
        "retract_length_toolchange": ["4", "4"],
        "retract_restart_extra_toolchange": ["0", "0"],
        "retract_lift_above": ["0", "0"],
        "retract_lift_below": ["0", "0"],
        "retract_lift_enforce": ["All Surfaces", "All Surfaces"],
        "wipe": ["1", "1"],
        "wipe_distance": ["2", "2"],
        "z_hop": ["0", "0"],
        "z_hop_types": ["Auto Lift", "Auto Lift"],
        "travel_slope": ["3", "3"],
        "long_retractions_when_cut": ["0", "0"],
        "retraction_distances_when_cut": ["18", "18"],

        "machine_max_speed_x": ["200"], "machine_max_speed_y": ["200"],
        "machine_max_speed_z": ["20"], "machine_max_speed_e": ["60"],
        "machine_max_acceleration_x": ["2000"], "machine_max_acceleration_y": ["2000"],
        "machine_max_acceleration_z": ["200"], "machine_max_acceleration_e": ["3000"],
        "machine_max_acceleration_extruding": ["2000"],
        "machine_max_acceleration_retracting": ["2000"],
        "machine_max_acceleration_travel": ["2000"],
        "machine_max_jerk_x": ["8"], "machine_max_jerk_y": ["8"],
        "machine_max_jerk_z": ["0.4"], "machine_max_jerk_e": ["5"],

        "machine_pause_gcode": "M25",
        "machine_start_gcode": start_gcode(mode),
        "machine_end_gcode": END_GCODE,
        "change_filament_gcode": ";switch nozzle\nM108 T[next_extruder]",
        "before_layer_change_gcode": ";layer:{layer_z}",
        "layer_change_gcode": "G92 E0",
        "time_lapse_gcode": "",
        "printing_by_object_gcode": "",
        "template_custom_gcode": "",

        "printer_notes": "\n".join([
            "PRINTER_VENDOR_FLASHFORGE",
            "PRINTER_MODEL_CREATOR_%s" % hotend.replace("-", "_"),
            "Creator %s: %s." % (hotend, spec["blurb"]),
            "Extruder 1 = T0 = RIGHT head, Extruder 2 = T1 = LEFT head.",
            "Bed origin is the CENTRE of the plate (-200..200 X, -175..175 Y), as in FlashPrint.",
            "Output is post-processed into .gx - see the process preset's post-processing script.",
        ]),
    }


# --------------------------------------------------------------------------
# processes
# --------------------------------------------------------------------------
TIERS = {
    # name: (speed multiplier, volumetric ceiling mm3/s, acceleration)
    "Standard": (1.0, 12.0, 1000),
    "Fast": (1.6, 16.0, 2000),
}
MAX_PRINT_SPEED = 120    # mm/s - the gantry carries two heads, it is not a Bambu
BASE_SPEEDS = {          # 0.4 nozzle, Standard tier - measured off the FlashPrint file
    "outer_wall_speed": 35, "inner_wall_speed": 60, "sparse_infill_speed": 80,
    "internal_solid_infill_speed": 60, "top_surface_speed": 40,
    "gap_infill_speed": 30, "bridge_speed": 25, "support_speed": 50,
    "support_interface_speed": 40, "initial_layer_speed": 20,
    "initial_layer_infill_speed": 40,
}


def process_name(layer, tier, nozzle):
    return "%.2fmm %s @FF Creator 4 %s" % (layer, tier, nozzle)


def process(layer, tier, nozzle, machines):
    mult, vol_cap, accel = TIERS[tier]
    width = round(nozzle * 1.05, 2)

    def speed(v):
        """Scale by tier, then clamp so the extruder is not asked for more
        plastic per second than the hot end can melt."""
        v = v * mult
        ceiling = vol_cap / (layer * width)
        return int(round(min(v, ceiling, MAX_PRINT_SPEED)))

    d = {
        "type": "process",
        "name": process_name(layer, tier, nozzle),
        "from": "User",
        "inherits": "0.20mm Standard @MyMarlin",
        "instantiation": "true",
        "print_settings_id": process_name(layer, tier, nozzle),
        "version": "2.4.0.0",
        "compatible_printers": machines,

        "layer_height": str(layer),
        "initial_layer_print_height": str(round(min(0.3, nozzle * 0.5), 2)),
        "line_width": str(round(nozzle * 1.05, 2)),
        "initial_layer_line_width": str(round(nozzle * 1.25, 2)),
        "outer_wall_line_width": str(nozzle),
        "inner_wall_line_width": str(round(nozzle * 1.125, 2)),
        "sparse_infill_line_width": str(round(nozzle * 1.125, 2)),
        "internal_solid_infill_line_width": str(round(nozzle * 1.05, 2)),
        "top_surface_line_width": str(nozzle),
        "support_line_width": str(round(nozzle * 0.95, 2)),

        "wall_loops": "3" if nozzle <= 0.6 else "2",
        "top_shell_layers": "4",
        "bottom_shell_layers": "3",
        "sparse_infill_density": "15%",
        "sparse_infill_pattern": "grid",

        "default_acceleration": str(accel),
        "initial_layer_acceleration": "500",
        "outer_wall_acceleration": str(int(accel * 0.8)),
        "inner_wall_acceleration": str(accel),
        "top_surface_acceleration": str(int(accel * 0.8)),
        "travel_acceleration": "2000",
        "travel_speed": "150" if tier == "Fast" else "100",

        "enable_prime_tower": "1",
        "prime_tower_width": "60",
        # Two separate nozzles: a tool change does not push one colour through
        # the other, it only re-primes a nozzle that has been sitting. Orca's
        # default would purge ~84 mm3 per change; this asks for ~14.
        "flush_multiplier": "0.05",
        "flush_into_infill": "1",
        "flush_into_objects": "0",
        "flush_into_support": "1",
        "prime_tower_brim_width": "3",
        "wipe_tower_x": ["-180", "-180"],
        "wipe_tower_y": ["-160", "-160"],

        "brim_type": "auto_brim",
        "brim_width": "0",
        "skirt_loops": "1",
        "skirt_distance": "3",
        "enable_arc_fitting": "0",

        "filename_format": "{input_filename_base}.gx",
        "post_process": [GX_POST],
        "notes": "Generated by generate.py. Speeds are capped at %g mm3/s of flow."
                 % vol_cap,
    }
    for k, v in BASE_SPEEDS.items():
        d[k] = str(speed(v))
    return d


# --------------------------------------------------------------------------
# filaments
# --------------------------------------------------------------------------
# (name, parent, type, nozzle temps, bed temps, side fan?, min hotend)
FILAMENTS = [
    ("Flashforge Creator 4 PLA", "Generic PLA @System", "PLA", 205, 200, 55, 100, "4-A"),
    ("Flashforge Creator 4 PETG", "Generic PETG @System", "PETG", 240, 235, 70, 50, "4-A"),
    ("Flashforge Creator 4 ABS", "Generic ABS @System", "ABS", 250, 245, 100, 0, "4-A"),
    ("Flashforge Creator 4 ASA", "Generic ASA @System", "ASA", 255, 250, 100, 0, "4-A"),
    ("Flashforge Creator 4 TPU", "Generic TPU @System", "TPU", 230, 230, 45, 60, "4-A"),
    ("Flashforge Creator 4 PC", "Generic PC @System", "PC", 280, 275, 110, 0, "4-S"),
    ("Flashforge Creator 4 PA-CF", "Generic PA-CF @System", "PA-CF", 290, 285, 100, 0, "4-S"),
]


def filament(spec, machines):
    name, parent, kind, t_first, t, bed, fan, min_hotend = spec
    beds = {}
    for key in ("cool_plate", "eng_plate", "hot_plate", "textured_plate",
                "supertack_plate"):
        beds[key + "_temp"] = [str(bed)]
        beds[key + "_temp_initial_layer"] = [str(bed)]
    d = {
        "type": "filament",
        "name": name,
        "from": "User",
        "inherits": parent,
        "instantiation": "true",
        "filament_settings_id": name,
        "version": "2.4.0.0",
        "compatible_printers": machines,
        "filament_extruder_variant": ["Direct Drive Standard"],

        "filament_vendor": ["Flashforge"],
        "filament_type": [kind],
        "filament_diameter": ["1.75"],
        "nozzle_temperature_initial_layer": [str(t_first)],
        "nozzle_temperature": [str(t)],
        "fan_min_speed": [str(fan)],
        "fan_max_speed": [str(fan)],
        "close_fan_the_first_x_layers": ["1"],
        "filament_start_gcode": [""],      # library presets poke fans this printer lacks
        "filament_end_gcode": [""],
        "filament_retraction_length": ["nil"],
    }
    d.update(beds)
    return d


# --------------------------------------------------------------------------
def main():
    for sub in ("machine", "process", "filament"):
        path = os.path.join(OUT, sub)
        if not os.path.isdir(path):
            os.makedirs(path)
        for old in os.listdir(path):
            os.remove(os.path.join(path, old))

    machines = []
    for hotend in HOTENDS:
        for nozzle in NOZZLES:
            for mode in IDEX_MODES:
                m = machine(hotend, nozzle, mode)
                machines.append(m["name"])
                write("machine", m)

    for nozzle in NOZZLES:
        # a process only fits printers with a matching nozzle
        fits = [n for n in machines if " %s nozzle" % nozzle in n]
        for layer in LAYERS[nozzle]:
            for tier in TIERS:
                write("process", process(layer, tier, nozzle, fits))

    for spec in FILAMENTS:
        min_hotend = spec[-1]
        allowed = [h for h in HOTENDS
                   if list(HOTENDS).index(h) >= list(HOTENDS).index(min_hotend)]
        fits = [n for n in machines
                if any(" Creator %s " % h in n for h in allowed)]
        write("filament", filament(spec, fits))

    bundle()
    print("machines: %d  processes: %d  filaments: %d"
          % (len(machines),
             len(os.listdir(os.path.join(OUT, "process"))),
             len(os.listdir(os.path.join(OUT, "filament")))))


def write(kind, preset):
    with open(os.path.join(OUT, kind, preset["name"] + ".json"), "w") as fh:
        json.dump(preset, fh, indent=4)
        fh.write("\n")


def bundle():
    """A .orca_printer is just a zip of presets; Orca imports them as user presets."""
    path = os.path.join(HERE, "dist", "FlashForge Creator 4.orca_printer")
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for kind in ("machine", "process", "filament"):
            for f in sorted(os.listdir(os.path.join(OUT, kind))):
                z.write(os.path.join(OUT, kind, f), f)


if __name__ == "__main__":
    main()
