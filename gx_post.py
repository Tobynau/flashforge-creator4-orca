#!/usr/bin/env python3
"""
OrcaSlicer -> FlashForge Creator 4 post-processor.

Used as an Orca "post-processing script". It does two things to the G-code
file Orca just wrote:

  1. Rewrites Marlin-isms into the FlashForge dialect the Creator 4 firmware
     speaks (M108 tool changes, M6/M7 temperature waits, T-suffixed fan and
     heater commands, absolute extruder addressing, no leading-dot numbers).
  2. Wraps the result in the binary .gx container FlashPrint produces:
     58-byte header + 80x60 24-bit BMP preview + the G-code.

Everything here was derived from a working FlashPrint .gx file for this
printer, so the emitted file should be byte-compatible with what the machine
expects.

Usage (Orca calls it like this):  python3 gx_post.py <file>
Standalone:                       python3 gx_post.py foo.gcode [-o foo.gx]
"""

import base64
import os
import re
import math
import struct
import sys
import zlib

MAGIC = b"xgcode 1.0\n\x00"
ACCEL = 1000.0                                     # mm/s^2, for the fallback estimate
SHORT_MOVE = 0.75                                  # mm, below which moves chain together
BMP_W, BMP_H = 80, 60


# --------------------------------------------------------------------------
# tiny PNG reader (Orca embeds its preview as a base64 PNG comment block)
# --------------------------------------------------------------------------
def png_to_rgb(data):
    """Return (width, height, [ (r,g,b), ... ]) or None if unsupported."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, w = 8, b"", None
    while pos + 8 <= len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or interlace != 0 or color not in (2, 6, 0, 4):
                return None
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    if w is None:
        return None
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    raw = zlib.decompress(idat)
    stride = w * channels
    out, prev = [], bytearray(stride)
    p = 0
    for _ in range(h):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            x = line[i]
            if f == 1:
                x += a
            elif f == 2:
                x += b
            elif f == 3:
                x += (a + b) >> 1
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                x += a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            line[i] = x & 0xFF
        prev = line
        row = []
        for i in range(w):
            px = line[i * channels:(i + 1) * channels]
            if channels >= 3:
                row.append((px[0], px[1], px[2]))
            else:
                row.append((px[0], px[0], px[0]))
        out.append(row)
    return w, h, out


def encode_png(pixels):
    """Minimal RGB PNG writer, for when Orca gave us no preview to pass on."""
    h, w = len(pixels), len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\x0a".replace(b"\x1a\x0a", b"\x1a\n")
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


def make_bmp(pixels):
    """80x60 24-bit BMP, bottom-up, exactly like FlashPrint's preview."""
    body = bytearray()
    for y in range(BMP_H - 1, -1, -1):
        for x in range(BMP_W):
            r, g, b = pixels[y][x]
            body += bytes((b, g, r))
    size = 54 + len(body)
    hdr = struct.pack("<2sIHHI", b"BM", size, 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, BMP_W, BMP_H, 1, 24, 0, len(body), 0, 0, 0, 0)
    return hdr + dib + bytes(body)


def scale_to_thumb(w, h, rows):
    """Nearest-neighbour fit of an arbitrary image into 80x60 (letterboxed)."""
    out = [[(0, 0, 0)] * BMP_W for _ in range(BMP_H)]
    scale = min(BMP_W / w, BMP_H / h)
    tw, th = max(1, int(w * scale)), max(1, int(h * scale))
    ox, oy = (BMP_W - tw) // 2, (BMP_H - th) // 2
    for y in range(th):
        sy = min(h - 1, int(y / scale))
        for x in range(tw):
            sx = min(w - 1, int(x / scale))
            out[oy + y][ox + x] = rows[sy][sx]
    return out


def blank_thumb():
    return [[(32, 32, 32)] * BMP_W for _ in range(BMP_H)]


# --------------------------------------------------------------------------
# G-code rewriting
# --------------------------------------------------------------------------
LEADING_DOT = re.compile(r"(?<=[XYZEFIJ])(-?)\.")
NUM = r"(-?\d+(?:\.\d+)?)"


def field(line, letter):
    """Value of a parameter, ignoring anything in a trailing comment."""
    m = re.search(r"(?<![A-Za-z])%s%s" % (letter, NUM), line.split(";", 1)[0])
    return m.group(1) if m else None


def add_param(line, param):
    """Append a parameter before the comment, not after it."""
    if ";" in line:
        code, comment = line.split(";", 1)
        return "%s %s ;%s" % (code.rstrip(), param, comment)
    return "%s %s" % (line, param)


def parse_time(text):
    total = 0
    for value, unit in re.findall(r"(\d+)\s*([dhms])", text):
        total += int(value) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def convert(lines, max_z=None, total_layers=0):
    """Return (converted_lines, info dict)."""
    out = []
    tool = 0
    relative_e = False
    e_abs = 0.0
    feed = 3600.0
    seconds = 0.0
    used = [0.0, 0.0]
    minx = miny = 1e9
    maxx = maxy = -1e9
    maxz = 0.0
    x = y = z = 0.0
    temps = {}
    bed_temp = 0
    in_thumb = False
    m118_at = None
    layer_count_at = None
    first_layer_at = None

    for raw in lines:
        line = raw.rstrip("\r\n")
        stripped = line.strip()

        # drop Orca's base64 preview block - the .gx header carries the image
        if stripped.startswith("; THUMBNAIL_BLOCK_START") or stripped.startswith("; thumbnail begin"):
            in_thumb = True
        if in_thumb:
            if stripped.startswith("; thumbnail end") or stripped.startswith("; THUMBNAIL_BLOCK_END"):
                in_thumb = False
            continue

        if not stripped or stripped.startswith(";"):
            m = re.match(r";layer_count:\s*(\d+)\s*$", stripped)
            if m:
                layer_count_at = len(out)
                out.append(";layer_count: %s" % m.group(1))
                continue
            m = re.match(r";layer:\s*([\d.]+)\s*$", stripped)
            if m:
                if first_layer_at is None:
                    first_layer_at = len(out)
                out.append(";layer:%.2f" % float(m.group(1)))
                continue
            out.append(line)
            continue

        code = stripped.split()[0].upper()

        # commands the FlashForge firmware has no use for
        if code in ("M73", "M75", "M76", "M77", "G21"):
            continue

        # the printer is always in absolute-E mode, like FlashPrint
        if code == "M83":
            relative_e = True
            continue
        if code == "M82":
            relative_e = False
            continue

        if code == "G92":
            ev = field(line, "E")
            if ev is not None:
                e_abs = float(ev)
            out.append(line)
            continue

        if code == "M84":
            out.append("M18")
            continue

        # bare tool change -> FlashForge tool select
        m = re.fullmatch(r"T(\d+)", stripped)
        if m:
            tool = int(m.group(1))
            out.append("M108 T%d" % tool)
            continue

        if code == "M108":
            t = field(line, "T")
            if t is not None:
                tool = int(float(t))
            out.append(line)
            continue

        # M109 (wait for nozzle) -> M104 + M6
        if code == "M109":
            s = field(line, "S") or field(line, "R") or "0"
            t = field(line, "T")
            t = int(float(t)) if t is not None else tool
            temps.setdefault(t, int(float(s)))
            out.append("M104 S%d T%d" % (int(float(s)), t))
            out.append("M6 T%d" % t)
            continue

        # M190 (wait for bed) -> M140 + M7
        if code == "M190":
            s = int(float(field(line, "S") or 0))
            bed_temp = bed_temp or s
            out.append("M140 S%d T0" % s)
            out.append("M7 T0")
            continue

        if code == "M104":
            t = field(line, "T")
            s = field(line, "S")
            if t is None:
                line = add_param(line, "T%d" % tool)
                t = tool
            t = int(float(t))
            if s and float(s) > 0:
                temps.setdefault(t, int(float(s)))
            if m118_at is None:
                m118_at = len(out)
            out.append(line)
            continue

        if code == "M140":
            if field(line, "T") is None:
                line = add_param(line, "T0")
            s = field(line, "S")
            if s and float(s) > 0 and not bed_temp:
                bed_temp = int(float(s))
            if m118_at is None:
                m118_at = len(out)
            out.append(line)
            continue

        if code == "M106":
            if field(line, "P") is not None:      # auxiliary/chamber fan
                continue
            if field(line, "T") is None:
                line = add_param(line, "T%d" % tool)
            out.append(line)
            continue

        if code == "M107":
            if field(line, "T") is None:
                line = add_param(line, "T%d" % tool)
            out.append(line)
            continue

        if code in ("G0", "G1", "G2", "G3"):
            line = LEADING_DOT.sub(r"\g<1>0.", line)
            ev = field(line, "E")
            e_prev = e_abs
            if ev is not None:
                if relative_e:
                    # FlashPrint keeps E absolute and zeroes it every ~1000mm
                    if e_abs + float(ev) > 1000.0:
                        out.append("G92 E0")
                        e_abs = 0.0
                        e_prev = 0.0
                    e_abs += float(ev)
                    line = re.sub(r"(?<![A-Za-z])E%s" % NUM,
                                  "E%.4f" % e_abs, line, count=1)
                else:
                    e_abs = float(ev)
            nx, ny, nz = field(line, "X"), field(line, "Y"), field(line, "Z")
            nf = field(line, "F")
            if nf is not None:
                feed = max(1.0, float(nf))
            px, py, pz = x, y, z
            if nx is not None:
                x = float(nx)
            if ny is not None:
                y = float(ny)
            if nz is not None:
                z = float(nz)
            dist = math.sqrt((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2)
            if ev is not None:
                de = e_abs - e_prev
                if de > 0 and (nx is not None or ny is not None):
                    used[min(tool, 1)] += de
                if dist == 0.0:
                    dist = abs(de)
            v = feed / 60.0
            if dist:
                # Short segments run back-to-back without stopping, so only
                # longer moves pay the accelerate/decelerate penalty. Within
                # ~5% of Orca's own estimate on the files tested.
                if dist < SHORT_MOVE:
                    seconds += dist / v
                elif dist >= v * v / ACCEL:
                    seconds += dist / v + v / ACCEL
                else:
                    seconds += 2.0 * math.sqrt(dist / ACCEL)
            if field(line, "E") is not None and (nx is not None or ny is not None):
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
                maxz = max(maxz, z)
            out.append(line)
            continue

        out.append(line)

    if maxx < minx:
        minx = miny = maxx = maxy = 0.0
    if max_z:
        maxz = max_z
    info = {
        "size": (maxx - minx, maxy - miny, maxz),
        "temps": temps,
        "bed_temp": bed_temp,
        "seconds": seconds,
        "used": used,
    }

    if layer_count_at is None and first_layer_at is not None and total_layers:
        out.insert(first_layer_at, ";layer_count: %d" % total_layers)
        if m118_at is not None and m118_at > first_layer_at:
            m118_at += 1

    # If nothing ever selected a tool, the printer keeps whatever head was last
    # active - which prints nothing when that head is not the loaded one.
    if not any(l.startswith("M108") for l in out):
        want = max(temps, key=lambda t: temps[t]) if temps else 0
        anchor = m118_at if m118_at is not None else 0
        out.insert(anchor, "M108 T%d" % want)
        if m118_at is not None:
            m118_at += 1

    # FlashPrint announces the model's bounding box before it heats up
    if m118_at is not None:
        out.insert(m118_at, "M118 X%.2f Y%.2f Z%.2f T0"
                   % (info["size"][0], info["size"][1], info["size"][2]))
    return out, info


def scrape(text):
    """Pull the numbers the .gx header wants out of Orca's comment blocks."""
    def find(pattern, default=None, cast=str):
        m = re.search(pattern, text, re.M)
        if not m:
            return default
        try:
            return cast(m.group(1))
        except ValueError:
            return default

    seconds = 0
    m = re.search(r"^; estimated printing time \(normal mode\) = (.+)$", text, re.M)
    if m:
        seconds = parse_time(m.group(1))

    used = [0.0, 0.0]
    m = re.search(r"^; filament used \[mm\] = (.+)$", text, re.M)
    if m:
        for i, v in enumerate(m.group(1).split(",")[:2]):
            try:
                used[i] = float(v)
            except ValueError:
                pass

    return {
        "seconds": seconds,
        "used": used,
        "layer_height": find(r"^; layer_height = ([\d.]+)", 0.2, float),
        "shells": find(r"^; wall_loops = (\d+)", 3, int),
        "speed": find(r"^; inner_wall_speed = ([\d.]+)", 60, float),
        "layers": find(r"^; total layer number: (\d+)", 0, int),
        "material": find(r"^; filament_type = ([A-Za-z0-9-]+)", "PLA"),
        "material2": find(r"^; filament_type = [A-Za-z0-9-]+,([A-Za-z0-9-]+)", None) or
                     find(r"^; filament_type = ([A-Za-z0-9-]+)", "PLA"),
        "density": find(r"^; filament_density = ([\d.]+)", 1.24, float),
        "fill": find(r"^; sparse_infill_density = (\d+%?)", "15%"),
        "travel": find(r"^; travel_speed = ([\d.]+)", 100, float),
        "max_z": find(r"^; max_z_height: ([\d.]+)", None, float),
        "nozzle_temps": find(r"^; nozzle_temperature = ([\d, ]+)", ""),
        "top_layers": find(r"^; top_shell_layers = (\d+)", 4),
        "bottom_layers": find(r"^; bottom_shell_layers = (\d+)", 3),
        "fill_pattern": find(r"^; sparse_infill_pattern = (\w+)", "grid"),
    }


def build_header(meta, png_offset, gcode_offset):
    h = bytearray(58)
    h[0:12] = MAGIC
    struct.pack_into("<I", h, 12, 0)
    struct.pack_into("<I", h, 16, 58)               # BMP offset
    struct.pack_into("<I", h, 20, png_offset)       # PNG preview offset
    struct.pack_into("<I", h, 24, gcode_offset)     # gcode offset
    struct.pack_into("<I", h, 28, int(meta["seconds"]))
    struct.pack_into("<I", h, 32, int(meta["used"][0]))
    struct.pack_into("<I", h, 36, int(meta["used"][1]))
    struct.pack_into("<H", h, 40, 15)               # multi-extruder flag
    struct.pack_into("<H", h, 42, int(round(meta["layer_height"] * 1000)))
    struct.pack_into("<H", h, 44, 0)
    struct.pack_into("<H", h, 46, int(meta["shells"]))
    struct.pack_into("<H", h, 48, int(round(meta["speed"])))
    struct.pack_into("<H", h, 50, int(meta["bed_temp"]))
    struct.pack_into("<H", h, 52, int(meta["t0"]))
    struct.pack_into("<H", h, 54, int(meta["t1"]))
    h[56] = 1
    h[57] = 1
    return bytes(h)


def ff_header_comments(meta):
    return "\n".join([
        ";generated by OrcaSlicer via gx_post.py",
        ";gcode_flavor: flashforge",
        ";machine_type: Creator 4",
        ";right_extruder_material: %s" % meta["material"],
        ";left_extruder_material: %s" % meta["material2"],
        ";right_extruder_material_density: %s" % meta["density"],
        ";filament_diameter0: 1.75",
        ";right_extruder_temperature: %d" % meta["t0"],
        ";left_extruder_temperature: %d" % meta["t1"],
        ";layer_height: %s" % meta["layer_height"],
        ";perimeter_shells: %d" % meta["shells"],
        ";top_solid_layers: %s" % meta["top_layers"],
        ";bottom_solid_layers: %s" % meta["bottom_layers"],
        ";fill_pattern: %s" % meta["fill_pattern"],
        ";fill_density: %s" % meta["fill"],
        ";base_print_speed: %d" % round(meta["speed"]),
        ";travel_speed: %d" % round(meta["travel"]),
        ";platform_temperature: %d" % meta["bed_temp"],
        ";right_extruder_temperature_raft0: %d" % meta["t0"],
        ";right_extruder_temperature_reset: ",
        ";left_extruder_temperature_reset: ",
        ";layer_total_count: %d" % meta["layers"],
        "",
    ])


def extract_thumbnail(text):
    """Return (80x60 pixel rows for the BMP, raw PNG bytes for the second image).

    FlashPrint stores both a BMP and a PNG preview; the firmware wants to find
    both, so a missing one is synthesised rather than skipped.
    """
    m = re.search(r"; thumbnail begin \d+[ x]\d+ \d+\s*\n(.*?); thumbnail end",
                  text, re.S)
    png = None
    img = None
    if m:
        b64 = "".join(l.strip().lstrip(";").strip() for l in m.group(1).splitlines())
        try:
            png = base64.b64decode(b64)
            img = png_to_rgb(png)
        except Exception:
            png, img = None, None
    if not img:
        rows = blank_thumb()
        return rows, encode_png([[(32, 32, 32)] * 320 for _ in range(320)])
    w, h, rows = img
    small = rows if (w, h) == (BMP_W, BMP_H) else scale_to_thumb(w, h, rows)
    return small, png


def show_info(path):
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:6] != b"xgcode":
        print("%s: NOT a .gx file - starts with %r" % (path, blob[:20]))
        print("  (the post-processing script did not run on this one)")
        return 1
    f = struct.unpack_from("<IIIIIIIHHHHHHHH", blob, 12)
    secs = f[4]
    print("%s" % path)
    print("  previews    : BMP @%d (%d bytes), PNG @%d (%d bytes)"
          % (f[1], f[2] - f[1], f[2], f[3] - f[2]))
    print("  print time  : %d s  (%dh %02dm)" % (secs, secs // 3600, secs % 3600 // 60))
    print("  filament    : right %d mm, left %d mm" % (f[5], f[6]))
    print("  layer height: %.2f mm   shells: %d   speed: %d mm/s"
          % (f[8] / 1000.0, f[10], f[11]))
    print("  temps       : bed %d, right %d, left %d" % (f[12], f[13], f[14]))
    return 0


def dump_gcode(path):
    """Print the G-code back out of a .gx, for when you want to read it."""
    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:6] != b"xgcode":
        sys.stdout.write(blob.decode("utf-8", "replace"))
        return 0
    off = struct.unpack_from("<I", blob, 24)[0]
    sys.stdout.write(blob[off:].decode("utf-8", "replace"))
    return 0


def main(argv):
    if "--dump" in argv:
        rest = [a for a in argv[1:] if not a.startswith("-")]
        return dump_gcode(rest[0]) if rest else 2
    if "--info" in argv:
        rest = [a for a in argv[1:] if not a.startswith("-")]
        return max(show_info(a) for a in rest) if rest else 2
    args = [a for a in argv[1:] if not a.startswith("-")]
    if not args:
        sys.stderr.write("usage: gx_post.py <gcode file>\n")
        return 2
    path = args[0]
    out_path = path
    if "-o" in argv:
        out_path = argv[argv.index("-o") + 1]

    with open(path, "rb") as fh:
        blob = fh.read()
    if blob[:6] == b"xgcode":
        return 0                                   # already converted

    text = blob.decode("utf-8", "replace")
    thumb, png = extract_thumbnail(text)
    meta = scrape(text)
    lines, info = convert(text.splitlines(), meta["max_z"], meta["layers"])
    meta["bed_temp"] = info["bed_temp"]
    configured = [int(float(v)) for v in meta["nozzle_temps"].split(",") if v.strip()]
    meta["t0"] = configured[0] if configured else info["temps"].get(0, 0)
    meta["t1"] = (configured[1] if len(configured) > 1 else meta["t0"])
    # Orca's own estimates are preferred; the ones measured off the toolpaths
    # are the safety net, because a .gx with a zeroed header shows 0:00:00 on
    # the printer.
    if not meta["seconds"]:
        meta["seconds"] = info["seconds"]
    if not any(meta["used"]):
        meta["used"] = info["used"]

    gcode = ff_header_comments(meta) + "\n".join(lines) + "\n"
    bmp = make_bmp(thumb)
    payload = gcode.encode("utf-8", "replace")
    header = build_header(meta, 58 + len(bmp), 58 + len(bmp) + len(png))

    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(header)
        fh.write(bmp)
        fh.write(png)
        fh.write(payload)
    os.replace(tmp, out_path)
    log(out_path, meta, len(bmp), len(png))
    return 0


def log(path, meta, bmp_len, png_len):
    """A breadcrumb, so it is obvious whether Orca actually ran this."""
    try:
        home = os.path.expanduser("~")
        with open(os.path.join(home, ".cache", "gx_post.log"), "a") as fh:
            fh.write("%s  time=%ds filament=%dmm/%dmm layer=%smm bed=%d "
                     "t0=%d t1=%d bmp=%d png=%d\n"
                     % (path, int(meta["seconds"]), int(meta["used"][0]),
                        int(meta["used"][1]), meta["layer_height"],
                        meta["bed_temp"], meta["t0"], meta["t1"],
                        bmp_len, png_len))
    except Exception:
        pass


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except BrokenPipeError:      # e.g. `--dump ... | head`
        os._exit(0)
