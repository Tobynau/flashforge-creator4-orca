#!/usr/bin/env python3
"""
Emits the Creator 4 profiles into an OrcaSlicer checkout, in the layout the
upstream repo expects.

    python3 upstream.py ../orcaslicer-src

Differences from the presets in this repo, all forced by the fact that a vendor
profile is JSON and nothing else - it cannot ship a post-processing script:

  * no post_process, no .gx filename format: output is plain G-code, which the
    printer also accepts
  * absolute extrusion, matching FlashPrint, rather than relative + conversion
  * prime tower off, because Orca only builds one with relative extrusion
  * no duplicate/mirror presets in this first pass - one thing at a time

Run scripts/assign_vendor_setting_ids.py in the checkout afterwards.
"""

import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "presets")
VENDOR = "Flashforge"
BED_MODEL = "flashforge_c4_buildplate_model.stl"
BED_TEXTURE = "flashforge_c4_buildplate_texture.svg"
BED_W, BED_D = 400.0, 350.0

HOTENDS = ["4-A", "4-S", "4-HT"]
NOZZLES = ["0.4", "0.6", "0.8"]

# Creator-4 variants of the vendor's generic filaments. The base profiles carry
# per-bed-type temperatures for machines with several build plates; this printer
# has one, so every plate temperature is the same figure.
FILAMENTS = [
    # suffix, base profile, first-layer nozzle, nozzle, bed, fan %
    ("PLA", "Flashforge Generic PLA", 205, 200, 55, 100),
    ("PETG", "Flashforge Generic PETG", 240, 235, 70, 50),
    ("ABS", "Flashforge Generic ABS", 250, 245, 100, 0),
    ("ASA", "Flashforge Generic ASA", 255, 250, 100, 0),
]
MATERIALS = ["Flashforge Generic %s @C4" % f[0] for f in FILAMENTS]

# our keys that mean nothing upstream
STRIP = {"from", "is_custom_defined", "printer_settings_id", "print_settings_id",
         "filament_settings_id", "version", "post_process", "filename_format",
         "thumbnails", "thumbnails_format", "notes", "compatible_printers"}


def load(kind, name):
    with open(os.path.join(SRC, kind, name + ".json")) as fh:
        return json.load(fh)


def machine_name(hotend, nozzle):
    return "Flashforge Creator %s %s nozzle" % (hotend, nozzle)


def process_name(layer, tier, nozzle):
    return "%s %s @FF C4 %s nozzle" % (layer, tier, nozzle)


def build_machine(hotend, nozzle):
    d = load("machine", machine_name(hotend, nozzle))
    for k in STRIP:
        d.pop(k, None)
    d["inherits"] = "fdm_flashforge_common"
    d["from"] = "system"
    d["instantiation"] = "true"
    d["printer_model"] = "Flashforge Creator %s" % hotend
    d["printer_variant"] = nozzle
    d["use_relative_e_distances"] = "0"        # absolute, as FlashPrint writes it
    # Orca rejects a G92 E0 in the layer change when extrusion is absolute -
    # it resets E itself in that mode
    d["layer_change_gcode"] = ""
    d["default_filament_profile"] = [MATERIALS[0]]
    d["default_print_profile"] = default_process(nozzle)
    return d


def default_process(nozzle):
    layers = {"0.4": "0.18mm", "0.6": "0.28mm", "0.8": "0.36mm"}
    return process_name(layers[nozzle], "Standard", nozzle)


def build_process(src_name, nozzle, machines):
    d = load("process", src_name)
    for k in STRIP:
        d.pop(k, None)
    layer, tier = src_name.split(" ")[0], src_name.split(" ")[1]
    d["name"] = process_name(layer, tier, nozzle)
    d["inherits"] = "fdm_process_common"
    d["from"] = "system"
    d["instantiation"] = "true"
    d["compatible_printers"] = machines
    # Orca only builds a prime tower with relative extrusion, and these
    # profiles are absolute. Two nozzles do not need one to change colour.
    d["enable_prime_tower"] = "0"
    for k in ("flush_multiplier", "flush_into_infill", "flush_into_objects",
              "flush_into_support", "prime_tower_width", "prime_tower_brim_width",
              "wipe_tower_x", "wipe_tower_y"):
        d.pop(k, None)
    return d


def write(root, sub, name, data):
    path = os.path.join(root, sub, name + ".json")
    with open(path, "w") as fh:
        json.dump(data, fh, indent="\t", ensure_ascii=False)
        fh.write("\n")
    return "%s/%s.json" % (sub, name)


def bed_model(path):
    """A plain 400 x 350 plate, 2 mm thick, sitting under the print area."""
    w, d, t = BED_W / 2, BED_D / 2, 2.0
    v = [(-w, -d, -t), (w, -d, -t), (w, d, -t), (-w, d, -t),
         (-w, -d, 0.0), (w, -d, 0.0), (w, d, 0.0), (-w, d, 0.0)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    out = bytearray(b"\0" * 80) + struct.pack("<I", len(faces))
    for a, b, c in faces:
        u = [v[b][i] - v[a][i] for i in range(3)]
        t2 = [v[c][i] - v[a][i] for i in range(3)]
        n = [u[1] * t2[2] - u[2] * t2[1], u[2] * t2[0] - u[0] * t2[2],
             u[0] * t2[1] - u[1] * t2[0]]
        m = math.sqrt(sum(x * x for x in n)) or 1.0
        out += struct.pack("<3f", *[x / m for x in n])
        for i in (a, b, c):
            out += struct.pack("<3f", *v[i])
        out += struct.pack("<H", 0)
    open(path, "wb").write(bytes(out))


def bed_texture(path):
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
             'viewBox="0 0 %d %d">' % (BED_W, BED_D, BED_W, BED_D),
             '  <rect width="%d" height="%d" fill="#4d5054"/>' % (BED_W, BED_D)]
    for x in range(50, int(BED_W), 50):
        lines.append('  <line x1="%d" y1="0" x2="%d" y2="%d" stroke="#606468" '
                     'stroke-width="1"/>' % (x, x, BED_D))
    for y in range(50, int(BED_D), 50):
        lines.append('  <line x1="0" y1="%d" x2="%d" y2="%d" stroke="#606468" '
                     'stroke-width="1"/>' % (y, BED_W, y))
    lines.append('  <line x1="%d" y1="0" x2="%d" y2="%d" stroke="#8a8f94" '
                 'stroke-width="2"/>' % (BED_W / 2, BED_W / 2, BED_D))
    lines.append('  <line x1="0" y1="%d" x2="%d" y2="%d" stroke="#8a8f94" '
                 'stroke-width="2"/>' % (BED_D / 2, BED_W, BED_D / 2))
    lines.append('</svg>')
    open(path, "w").write("\n".join(lines) + "\n")


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    checkout = os.path.abspath(argv[1])
    root = os.path.join(checkout, "resources", "profiles", VENDOR)
    if not os.path.isdir(root):
        print("not an OrcaSlicer checkout: %s" % checkout)
        return 1

    vendor_json = os.path.join(checkout, "resources", "profiles", VENDOR + ".json")
    vendor = json.load(open(vendor_json))

    models, machines, processes = [], [], []

    machine_names = [machine_name(h, n) for h in HOTENDS for n in NOZZLES]

    for hotend in HOTENDS:
        model = {
            "type": "machine_model",
            "name": "Flashforge Creator %s" % hotend,
            "model_id": "Flashforge-Creator-%s" % hotend,
            "nozzle_diameter": ";".join(NOZZLES),
            "machine_tech": "FFF",
            "family": VENDOR,
            "bed_model": BED_MODEL,
            "bed_texture": BED_TEXTURE,
            "hotend_model": "",
            "default_materials": ";".join(MATERIALS),
        }
        models.append((model["name"], write(root, "machine", model["name"], model)))
        for nozzle in NOZZLES:
            m = build_machine(hotend, nozzle)
            machines.append((m["name"], write(root, "machine", m["name"], m)))

    for nozzle in NOZZLES:
        fits = [n for n in machine_names if n.endswith(" %s nozzle" % nozzle)]
        for src in sorted(os.listdir(os.path.join(SRC, "process"))):
            if " @FF Creator 4 %s.json" % nozzle not in src:
                continue
            p = build_process(src[:-5], nozzle, fits)
            processes.append((p["name"], write(root, "process", p["name"], p)))

    filaments = []
    for suffix, base, t_first, t, bed, fan in FILAMENTS:
        name = "Flashforge Generic %s @C4" % suffix
        base_json = json.load(open(os.path.join(root, "filament", base + ".json")))
        d = {
            "type": "filament",
            "name": name,
            "inherits": base,
            "from": "system",
            "instantiation": "true",
            "filament_id": base_json.get("filament_id", ""),
            "compatible_printers": machine_names,
            "nozzle_temperature_initial_layer": [str(t_first)],
            "nozzle_temperature": [str(t)],
            "fan_min_speed": [str(fan)],
            "fan_max_speed": [str(fan)],
            "close_fan_the_first_x_layers": ["1"],
        }
        for plate in ("cool_plate", "eng_plate", "hot_plate", "textured_plate",
                      "supertack_plate"):
            d[plate + "_temp"] = [str(bed)]
            d[plate + "_temp_initial_layer"] = [str(bed)]
        filaments.append((name, write(root, "filament", name, d)))
    touched = len(filaments)

    bed_model(os.path.join(root, BED_MODEL))
    bed_texture(os.path.join(root, BED_TEXTURE))

    def merge(key, entries):
        have = {e["name"] for e in vendor[key]}
        for name, sub in entries:
            if name not in have:
                vendor[key].append({"name": name, "sub_path": sub})
    merge("machine_model_list", models)
    merge("machine_list", machines)
    merge("process_list", processes)
    merge("filament_list", filaments)

    major, minor, patch, build = vendor["version"].split(".")
    vendor["version"] = "%s.%s.%s.%02d" % (major, minor, patch, int(build) + 1)
    with open(vendor_json, "w") as fh:
        json.dump(vendor, fh, indent="\t", ensure_ascii=False)
        fh.write("\n")

    print("models %d, machines %d, processes %d, filaments %d"
          % (len(models), len(machines), len(processes), touched))
    print("vendor version -> %s" % vendor["version"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
