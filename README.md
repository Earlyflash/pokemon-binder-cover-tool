# Pokemon TCG Binder Cover Generator

Generates a print-ready, square binder-cover image (the "Pokedex" style panel we
designed for M2A Mega Brave and M5 Abyss Eye) for any Pokemon TCG set, straight
from the command line. No AI calls needed once it's set up.

Set details (code, English/Japanese name, era, release date, total cards) are
looked up automatically from [TCGdex](https://tcgdex.dev/), a free multilingual
Pokemon TCG database that covers Japan-exclusive sets -- you just give it the
set name or code. This means the tool needs internet access to run; if a set
isn't in TCGdex yet (e.g. it's brand new) or the lookup gets something wrong,
you can override any individual field with its own flag.

Output is a 3600x3600px PNG by default (12in x 12in at 300dpi) sized for a
9-pocket zip binder, sitting on a plain white background (with a soft drop
shadow) so you can crop it to whatever binder size you actually have without
wasting ink printing a dark background.

## Setup (one-time)

1. Install Python 3.9+ if you don't already have it.
2. In this folder, install the dependencies:

   ```
   pip install -r requirements.txt
   ```

   The `qrcode` package is only needed if you plan to use `--qr-url`. If you
   skip it, everything else still works -- you'll just get a note that the
   QR block was skipped.

3. Keep the `fonts/` folder next to `binder_cover.py` -- it bundles the
   Poppins and Lato weights the design uses, so your covers look the same
   regardless of what's installed on your PC. (Japanese text falls back to
   whatever CJK font your OS ships with -- Windows and macOS both have one
   built in; nothing to install there.)

## Basic usage

Not sure what a set's TCGdex code is, or whether it's indexed at all? List
everything TCGdex knows about first:

```
python binder_cover.py --list-sets
```

```
python binder_cover.py --set M5 --name "Abyss Eye" \
  --qr-url "https://rarecandy.com/pokemon/sets/abyss-eye?username=Earlyflash" \
  --qr-subcaption "My Collection" \
  --out M5_Abyss_Eye_cover.png
```

(`--name` is needed here because M5 hasn't released in English yet, so
TCGdex only has its Japanese name -- see "Japan-exclusive sets" below.
Everything else -- release date, total cards, the Main Set/Secret Rares
split -- comes from the lookup.)

Or the completion-gauge style (no QR code), like the original M2A cover:

```
python binder_cover.py --set M2A --name "Mega Brave" \
  --completion "187/187" \
  --out M2A_Mega_Brave_cover.png
```

Run `python binder_cover.py --help` any time for the full flag list.

## All the flags

| Flag | Required? | Description |
|---|---|---|
| `--set` | yes, unless `--list-sets` | Set name or code to look up on TCGdex, e.g. `M5` or `"Abyss Eye"`. |
| `--list-sets` | no | List every set code/name TCGdex knows about (both English and Japanese datasets), then exit without generating a cover. Handy for finding a set's exact code. |
| `--qr-url` | no | If set, shows a "scan to track" QR code linking here instead of a completion gauge. |
| `--set-code` | no | Override the looked-up set code, e.g. `M5`. |
| `--name` | no | Override the looked-up English set name, e.g. `Abyss Eye`. |
| `--name-jp` | no | Override the looked-up Japanese set name, e.g. `アビスアイ`. Skipped if unavailable, or if no CJK font can be found on your system. |
| `--game-title` | no | Top header line. Default `Pokemon Card Game`. |
| `--era` | no | Override the looked-up era/series subheading, e.g. `Mega Series` or `Scarlet & Violet Era`. Left out if unavailable. |
| `--release-date` | no | Override the looked-up release date, e.g. `22 MAY 2026`. |
| `--total-cards` | no | Override the looked-up total card count, e.g. `118`. |
| `--stat LABEL VALUE` | no | Up to two small stat rows on the bottom-left, e.g. `--stat "Secret Rares" 37`. Repeat the flag for a second row. Auto-filled as `Main Set` / `Secret Rares` from TCGdex's printed/grand-total card counts when the set has any secret rares and you don't pass `--stat` yourself; pass it to override with your own rows. |
| `--qr-caption` | no | Text above the QR code. Default `Scan To Track`. |
| `--qr-subcaption` | no | Small text below the QR code, e.g. `My Collection`. |
| `--completion` | no | `collected/total` (e.g. `187/187`) or a bare percent (e.g. `100`). Draws a circular gauge. Ignored if `--qr-url` is also given. Your own collection progress, so always manual. |
| `--footer` | no | Bottom banner text next to the Poke Ball icon. Default `Japanese Master Set`. |
| `--accent` | no | Hex accent color (no `#`), default `C42A22` (Poke Ball red). |
| `--accent-tab` / `--no-accent-tab` | no | Show/hide the small red tab on the right edge. Default on. |
| `--bg-color` | no | Hex color (no `#`) for the area outside the panel. Default `FFFFFF` (white), so printing doesn't waste ink. |
| `--size` | no | Canvas size in px (square). Default `3600` (12in @ 300dpi). |
| `--font-dir` | no | An extra folder to search for fonts first (useful if you want to swap in your own). |
| `-o, --out` | no | Output PNG path. Defaults to `<SET_CODE>_<name>_<EN\|JP>_cover.png` in the current folder -- `JP` if the cover shows a Japanese name, `EN` otherwise. |
| `-v, --verbose` | no | Prints which font file was used for each text role, and which TCGdex lookups were tried -- handy for troubleshooting. |

## Notes

- **Set lookup**: `--set` is matched against TCGdex first by exact set code,
  then by set name (Japanese sets are searched first, then English/
  international). If it can't find a unique match -- the set is too new,
  the name's ambiguous, or you're offline -- it prints a warning and leaves
  the affected fields blank. Fill in the gaps with `--set-code`, `--name`,
  `--name-jp`, `--era`, `--release-date`, and/or `--total-cards`; the tool
  only hard-fails if `--set-code`/`--name`/`--release-date`/`--total-cards`
  are still missing after the lookup.
- **Japan-exclusive sets**: for a set that hasn't released in English yet
  (e.g. `M5`/Abyss Eye), TCGdex only stores its Japanese name -- the English
  name you know it by is a fan translation that isn't in TCGdex at all.
  Search by the **set code** (`--set M5`) in that case, not the English
  name; once the set has an official English release, its name becomes
  searchable too.
- **QR codes**: install `qrcode[pil]` for the best/lightest QR generation.
  The script also has a `reportlab`-based fallback if that's what you happen
  to have installed instead.
- **Long names**: text that's too wide for the card (a long `--name`, a long
  `--footer`, etc.) automatically shrinks to fit -- you don't need to worry
  about it overflowing the panel.
- **Printing**: at the default 3600px size you've got clean 300dpi print
  quality up to 12x12in -- crop down in any image editor (or your printer's
  driver) to match your actual binder's cover-insert size.
- Re-run any time a new set drops -- just change `--set`.

## Running tests

```
python -m unittest discover -s tests -v
```

The suite mocks all TCGdex network calls, so it runs fast and doesn't need
internet access. It also runs automatically on every push/PR via GitHub
Actions (`.github/workflows/tests.yml`).
