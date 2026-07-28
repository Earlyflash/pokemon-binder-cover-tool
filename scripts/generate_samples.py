#!/usr/bin/env python3
"""
Regenerates every image in samples/ by invoking binder_cover.py exactly as a
user would from the command line (so it also acts as an end-to-end smoke
test of the real CLI, not just the internal functions the unit tests call).

Covers every current Mega Evolution-era set on TCGdex: the Japan-exclusive
m-codes (m1L, m2, m2a, m3, m4, m5 -- each needs --name/--name-jp since TCGdex
only has their Japanese names) and the English-released me-codes (me01
through me05, including the me02.5 half-set). Also includes one fully
synthetic set (m6) that TCGdex doesn't know about at all, to demonstrate the
--set-code/--name/--release-date/--total-cards/--era manual-override path.
Between them, the jobs below exercise every flag binder_cover.py has except
--font-dir (covered instead by tests/test_binder_cover.py's font-resolution
tests, since it needs a real alternate font file on disk to be meaningful).

Usage:
    python scripts/generate_samples.py                  # refreshes samples/ in place
    python scripts/generate_samples.py --output-dir DIR  # writes elsewhere instead (used by CI)
"""
import argparse
import os
import subprocess
import sys
import tempfile

try:
    # Some job commands print Japanese set names; legacy Windows console
    # codepages (e.g. cp1252) can't encode them and would otherwise crash
    # print() instead of just substituting '?' for the unsupported characters.
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
BINDER_COVER = os.path.join(REPO_ROOT, "binder_cover.py")
DEFAULT_SAMPLES_DIR = os.path.join(REPO_ROOT, "samples")

RARECANDY = "https://rarecandy.com/pokemon/sets"

# Each job is one full binder_cover.py invocation. `out` is the filename it's
# saved as (matching the tool's own <SET_CODE>_<name>_<EN|JP>_cover.png
# convention so a diff against the committed samples/ stays meaningful).
JOBS = [
    {
        "set": "m1L",
        "args": ["--name", "Mega Brave", "--name-jp", "メガブレイブ",
                 "--qr-url", f"{RARECANDY}/mega-brave?username=Earlyflash",
                 "--qr-subcaption", "My Collection"],
        "out": "M1L_Mega_Brave_JP_cover.png",
        "covers": "--qr-url, --qr-subcaption, Japan-exclusive --name/--name-jp override",
    },
    {
        "set": "m2",
        "args": ["--name", "Inferno X", "--name-jp", "インフェルノX",
                 "--completion", "80/116"],
        "out": "M2_Inferno_X_JP_cover.png",
        "covers": "--completion (collected/total form)",
    },
    {
        "set": "m2a",
        "args": ["--name", "MEGA Dream ex", "--name-jp", "MEGAドリームex",
                 "--completion", "100", "--stat", "Alt Arts", "12"],
        "out": "M2A_MEGA_Dream_ex_JP_cover.png",
        "covers": "--completion (bare percent form), single --stat override",
    },
    {
        "set": "m3",
        "args": ["--name", "Nihil Zero", "--name-jp", "ムニキスゼロ",
                 "--rarity-chart"],
        "out": "M3_Nihil_Zero_JP_cover.png",
        "covers": "--rarity-chart",
    },
    {
        "set": "m4",
        "args": ["--name", "Ninja Spinner", "--name-jp", "ニンジャスピナー",
                 "--accent", "1F6FEB", "--bg-color", "F4F1EA", "--no-accent-tab"],
        "out": "M4_Ninja_Spinner_JP_cover.png",
        "covers": "--accent, --bg-color, --no-accent-tab",
    },
    {
        "set": "m5",
        "args": ["--name", "Abyss Eye", "--name-jp", "アビスアイ",
                 "--game-title", "Pokemon TCG", "--footer", "Complete Japanese Set", "-v"],
        "out": "M5_Abyss_Eye_JP_cover.png",
        "covers": "--game-title, --footer, -v/--verbose, blank right column",
    },
    {
        "set": "m6",
        "args": ["--set-code", "M6", "--name", "Genesis Break",
                 "--release-date", "12 SEP 2026", "--total-cards", "130",
                 "--era", "Mega Evolution", "--footer", "Coming Soon",
                 "--stat", "Main Set", "94", "--stat", "Secret Rares", "36"],
        "out": "M6_Genesis_Break_EN_cover.png",
        "covers": "--set-code/--name/--release-date/--total-cards/--era manual override "
                  "for a set TCGdex has no record of at all, two --stat rows",
    },
    {
        "set": "me01",
        "args": [],
        "out": "ME01_Mega_Evolution_EN_cover.png",
        "covers": "defaults only (blank right column baseline)",
    },
    {
        "set": "me02",
        "args": ["--qr-url", f"{RARECANDY}/phantasmal-flames?username=Earlyflash",
                 "--qr-caption", "Track My Progress"],
        "out": "ME02_Phantasmal_Flames_EN_cover.png",
        "covers": "--qr-caption override",
    },
    {
        "set": "me02.5",
        "args": ["--completion", "217/295"],
        "out": "ME02.5_Ascended_Heroes_EN_cover.png",
        "covers": "--completion (collected/total form) on a half-set code with a dot",
    },
    {
        "set": "me03",
        "args": ["--stat", "Full Arts", "22", "--stat", "Gold Cards", "6"],
        "out": "ME03_Perfect_Order_EN_cover.png",
        "covers": "two --stat overrides on a set that already has looked-up stats",
    },
    {
        "set": "me04",
        "args": ["--completion", "100", "--accent", "2EA043", "--no-accent-tab"],
        "out": "ME04_Chaos_Rising_EN_cover.png",
        "covers": "--completion (bare percent form), --accent, --no-accent-tab",
    },
    {
        "set": "me05",
        "args": ["--qr-url", f"{RARECANDY}/pitch-black?username=Earlyflash",
                 "--qr-caption", "Scan To Track", "--qr-subcaption", "Master Set",
                 "--total-cards", "120"],
        "out": "ME05_Pitch_Black_EN_cover.png",
        "covers": "--qr-subcaption, explicit --total-cards override",
    },
]


def run_job(job, out_dir):
    out_path = os.path.join(out_dir, job["out"])
    cmd = [sys.executable, BINDER_COVER, "--set", job["set"], "--out", out_path, *job["args"]]
    print(f"\n=== {job['out']}  ({job['covers']}) ===")
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    return result.returncode == 0


def check_list_sets():
    print("\n=== --list-sets sanity check ===")
    result = subprocess.run([sys.executable, BINDER_COVER, "--list-sets"], cwd=REPO_ROOT)
    return result.returncode == 0


def check_size_flag():
    """--size isn't exercised by any gallery sample (they're all left at the
    default 3600 so the showcase images stay consistent), so prove it works
    with a disposable low-res render instead."""
    print("\n=== --size sanity check (disposable, not part of samples/) ===")
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "size_check.png")
        cmd = [sys.executable, BINDER_COVER, "--set", "me01", "--size", "300", "--out", out_path]
        print(" ".join(cmd))
        result = subprocess.run(cmd, cwd=REPO_ROOT)
        if result.returncode == 0 and os.path.isfile(out_path):
            from PIL import Image
            with Image.open(out_path) as im:
                if im.size != (300, 300):
                    print(f"[error] expected a 300x300 image, got {im.size}")
                    return False
            return True
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default=DEFAULT_SAMPLES_DIR,
                         help="Where to write the regenerated PNGs (default: samples/, i.e. "
                              "refresh the committed samples in place)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    failures = []
    for job in JOBS:
        if not run_job(job, args.output_dir):
            failures.append(job["out"])
    if not check_list_sets():
        failures.append("--list-sets")
    if not check_size_flag():
        failures.append("--size")

    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)} of {len(JOBS) + 2} checks did not succeed:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"OK: all {len(JOBS)} samples regenerated into {args.output_dir}, "
          "plus --list-sets and --size sanity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
