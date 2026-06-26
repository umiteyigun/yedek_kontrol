#!/bin/bash
# Yedek oncesi disk alani kontrolu — yetersizse exit 1
# Kullanim: disk-check-backup.sh <GUNLUK|HAFTALIK|RMAN_*> [instance_id]
set -euo pipefail

TIP="${1:-GUNLUK}"
INSTANCE_ID="${2:-}"

python - "$TIP" "$INSTANCE_ID" <<'PY'
from __future__ import print_function
import json
import os
import sys

tip = sys.argv[1]
instance_id = sys.argv[2] if len(sys.argv) > 2 else ""

config_path = "/yedek/config/disk-guard.json"
try:
    with open(config_path, "r") as handle:
        guard = json.load(handle)
except (IOError, ValueError):
    guard = {
        "yedek_dir": "/yedek/orayedek",
        "max_usage_pct": 90,
        "min_free_gb": 5.0,
        "reserve_gb": 2.0,
        "margin_pct": 25,
        "estimate_gunluk_gb": 3.0,
        "estimate_haftalik_gb": 10.0,
        "estimate_rman_full_gb": 25.0,
        "estimate_rman_incr_gb": 5.0,
        "weekly_size_factor": 1.35,
    }

yedek_dir = guard["yedek_dir"]
check_dir = yedek_dir
if tip.startswith("RMAN_") and instance_id:
    inst_path = os.path.join("/yedek/config/instances", instance_id + ".sh")
    if os.path.isfile(inst_path):
        with open(inst_path, "r") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("rman_dest="):
                    raw = line.split("=", 1)[1].strip().strip("'\"")
                    if raw:
                        check_dir = raw
                    break

if not os.path.isdir(check_dir):
    try:
        os.makedirs(check_dir)
    except OSError:
        pass

try:
    stat = os.statvfs(check_dir)
except OSError as exc:
    sys.stderr.write("Disk kontrolu basarisiz: {0}\n".format(exc))
    sys.exit(1)

total_gb = float(stat.f_blocks * stat.f_frsize) / (1024 ** 3)
free_gb = float(stat.f_bavail * stat.f_frsize) / (1024 ** 3)
used_pct = int(((total_gb - free_gb) / total_gb) * 100) if total_gb else 100

max_pct = int(guard["max_usage_pct"])
min_free = float(guard["min_free_gb"])
reserve = float(guard["reserve_gb"])
margin = 1.0 + float(guard["margin_pct"]) / 100.0
weekly_factor = float(guard.get("weekly_size_factor", 1.35))

if used_pct >= max_pct:
    sys.stderr.write("Disk doluluk limiti: %{0} (max %{1})\n".format(used_pct, max_pct))
    sys.exit(1)

if free_gb < min_free:
    sys.stderr.write("Yetersiz bos alan: {0:.1f}GB (min {1:.1f}GB)\n".format(free_gb, min_free))
    sys.exit(1)

if tip == "HAFTALIK":
    required_gb = float(guard["estimate_haftalik_gb"])
    source = "default_haftalik"
elif tip in ("RMAN_FULL", "RMAN_FULL_MANUAL"):
    required_gb = float(guard.get("estimate_rman_full_gb", 25.0))
    source = "default_rman_full"
elif tip == "RMAN_INCR":
    required_gb = float(guard.get("estimate_rman_incr_gb", 5.0))
    source = "default_rman_incr"
else:
    required_gb = float(guard["estimate_gunluk_gb"])
    source = "default_gunluk"

if not tip.startswith("RMAN_") and instance_id:
    archive = os.path.join(yedek_dir, instance_id + ".dmp.gz")
    if os.path.isfile(archive):
        last_gb = float(os.path.getsize(archive)) / (1024 ** 3)
        required_gb = last_gb * margin
        if tip == "HAFTALIK":
            required_gb *= weekly_factor
        source = "last_backup"

need_gb = required_gb + reserve
if free_gb < need_gb:
    sys.stderr.write(
        "Tahmini yedek {0:.1f}GB + rezerv {1:.1f}GB icin yetersiz alan "
        "(bos {2:.1f}GB, kaynak={3})\n".format(required_gb, reserve, free_gb, source)
    )
    sys.exit(1)

print("Disk OK: bos={0:.1f}GB kullanim=%{1} tahmin={2:.1f}GB kaynak={3}".format(
    free_gb, used_pct, required_gb, source
))
PY
