#!/usr/bin/env python3
"""
Installs the FlashForge Creator 4 presets into OrcaSlicer.

    python3 install.py              # find every Orca install and set it up
    python3 install.py --datadir /path/to/OrcaSlicer
    python3 install.py --uninstall

What it does, per install:
  * copies gx_post.py next to Orca's config
  * copies the presets into user/default/{machine,process,filament}
  * points each process preset's post-processing script at that copy
  * registers the "Generic Marlin Printer" model the presets inherit from,
    otherwise Orca skips presets whose parent is not installed
Close OrcaSlicer first - it rewrites its config on exit and would undo this.
"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = os.path.join(HERE, "presets")
KINDS = ("machine", "process", "filament")
PARENT_MODEL = {"model": "Generic Marlin Printer",
                "nozzle_diameter": "0.4",
                "vendor": "Custom Printer"}
INFO = "sync_info = \nuser_id = \nsetting_id = \nbase_id = \nupdated_time = 0\n"


def candidates():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Application Support", "OrcaSlicer")]
    if os.name == "nt":
        return [os.path.join(os.environ.get("APPDATA", ""), "OrcaSlicer")]
    return [
        os.path.join(home, ".config", "OrcaSlicer"),
        os.path.join(home, ".var", "app", "com.orcaslicer.OrcaSlicer",
                     "config", "OrcaSlicer"),
        os.path.join(home, ".var", "app", "io.github.orcaslicer.OrcaSlicer",
                     "config", "OrcaSlicer"),
        os.path.join(home, "snap", "orcaslicer", "current", ".config", "OrcaSlicer"),
    ]


def python_command(datadir):
    """Flatpak runs the script inside its sandbox, where the host's Python is
    not on the path - but the runtime ships one at a known location."""
    if ".var/app/" in datadir.replace("\\", "/"):
        return "/usr/bin/python3"
    if os.name == "nt":
        return '"%s"' % sys.executable
    return sys.executable


def orca_running():
    try:
        return subprocess.run(["pgrep", "-x", "orca-slicer"],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def install(datadir):
    script = os.path.join(datadir, "gx_post.py")
    shutil.copy2(os.path.join(HERE, "gx_post.py"), script)
    command = '%s "%s"' % (python_command(datadir), script)

    count = 0
    for kind in KINDS:
        src = os.path.join(PRESETS, kind)
        dst = os.path.join(datadir, "user", "default", kind)
        if not os.path.isdir(dst):
            os.makedirs(dst)
        for name in sorted(os.listdir(src)):
            preset = json.load(open(os.path.join(src, name)))
            if "post_process" in preset:
                preset["post_process"] = [command]
            with open(os.path.join(dst, name), "w") as fh:
                json.dump(preset, fh, indent=4)
            info = os.path.join(dst, name[:-5] + ".info")
            if not os.path.exists(info):
                open(info, "w").write(INFO)
            count += 1

    register_parent(datadir)
    print("  %d presets -> %s" % (count, datadir))
    print("  post-processing command: %s" % command)


def register_parent(datadir):
    conf = os.path.join(datadir, "OrcaSlicer.conf")
    if not os.path.exists(conf):
        print("  ! no OrcaSlicer.conf yet - run Orca once, then re-run this")
        return
    data = json.load(open(conf))
    models = data.setdefault("models", [])
    if any(m.get("model") == PARENT_MODEL["model"] for m in models):
        return
    shutil.copy2(conf, conf + ".bak-creator4")
    models.append(dict(PARENT_MODEL))
    with open(conf, "w") as fh:
        json.dump(data, fh, indent="\t")
    print("  registered %s (backup: OrcaSlicer.conf.bak-creator4)"
          % PARENT_MODEL["model"])


def uninstall(datadir):
    removed = 0
    for kind in KINDS:
        src = os.path.join(PRESETS, kind)
        dst = os.path.join(datadir, "user", "default", kind)
        if not os.path.isdir(dst):
            continue
        for name in os.listdir(src):
            for f in (name, name[:-5] + ".info"):
                path = os.path.join(dst, f)
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
    print("  removed %d files from %s" % (removed, datadir))


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__.strip())
        return 0
    if orca_running():
        print("OrcaSlicer is running - close it first, it overwrites its "
              "config on exit.")
        return 1
    action = uninstall if "--uninstall" in argv else install
    if "--datadir" in argv:
        targets = [argv[argv.index("--datadir") + 1]]
    else:
        targets = [d for d in candidates() if os.path.isdir(d)]
    if not targets:
        print("No OrcaSlicer config directory found. Run Orca once, or pass "
              "--datadir.")
        return 1
    for datadir in targets:
        print(os.path.basename(datadir) + ":")
        action(datadir)
    print("\nDone. Start Orca and pick your printer under "
          "Custom Printer -> Generic Marlin Printer.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
