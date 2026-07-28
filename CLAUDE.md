# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file CLI tool (`binder_cover.py`) that generates print-ready, square
Pokemon TCG binder cover images (a "Pokedex"-style panel) as 3600x3600px PNGs.
Everything — font resolution, QR generation, and pixel-level drawing — lives
in this one script. There is no build step, no test suite, and no package
structure; it's meant to be run directly.

## Commands

Setup:
```
pip install -r requirements.txt
```
`qrcode[pil]` (in requirements.txt) is optional and only used by `--qr-url`;
the script degrades gracefully (prints a warning, skips the QR block) if it's
missing. There's also a `reportlab`-based QR fallback if `qrcode` isn't
installed but `reportlab` is.

Run the tool:
```
python binder_cover.py --set M5 --name "Abyss Eye" --out test_cover.png
```
`--set` (code or name) triggers a live lookup against TCGdex
(api.tcgdex.net) to fill in set code/name/name_jp/era/release date/total
cards/main-secret-rare split; any of those can still be overridden with
their own flag (`--name`, `--release-date`, etc.) when TCGdex has it wrong
or doesn't have the set yet. This means the tool needs internet access by
default. See README.md for the full example invocations (QR-code style vs.
completion-gauge style), the full flag table, and the Japan-exclusive-set
caveats (TCGdex only indexes a set's English name once it has an English
release — search those by set code, not fan translation).

Debugging font selection: pass `-v/--verbose` to print which font file was
resolved for each text role (bundled vs. system match vs. not found).

There is no lint/test/build command — verify changes by actually running the
script and inspecting the output PNG.

## Architecture

Everything is in `binder_cover.py`, organized into four sections (see the
`# ---- comment ----` banners in the file):

1. **Font resolution** (`resolve_font_path`, `get_font`, `_build_font_index`,
   `fit_font`). Fonts are looked up by abstract "role" (e.g.
   `black_display`, `bold_caps`, `medium`, `italic`, `japanese`), not by
   filename. For each role, `BUNDLED` maps to a font shipped in `fonts/`
   (Poppins/Lato weights); if that's missing, `ROLE_KEYWORDS` gives a list of
   filename substrings to search for across OS font directories (Windows
   Fonts, macOS System/Library Fonts, Linux `/usr/share/fonts` etc.), walked
   and cached once in `_FONT_INDEX_CACHE`. Japanese text relies entirely on
   whatever CJK font the OS provides (Noto/Meiryo/Yu Gothic/Hiragino/MS
   Gothic) — there's no bundled CJK font — and is silently skipped with a
   warning if none is found. `fit_font` shrinks a font's point size in a loop
   until the rendered text fits a target pixel width, which is how the tool
   avoids ever overflowing the panel with long set names/footers.

2. **Drawing primitives** (`draw_spaced`, `measure_spaced`, `centered`,
   `pokeball`, `make_qr_image`, `hex_to_rgb`). Low-level Pillow helpers:
   letter-spaced text (used everywhere for the all-caps display text),
   centered text, the Poke Ball icon drawn as concentric ellipses/pieslices,
   and QR image generation with a `qrcode` → `reportlab` → skip fallback
   chain.

3. **Set lookup** (`lookup_set_info`, `_find_set_id`, `_fetch_json`,
   `_format_release_date`, `_is_latin_text`). Queries TCGdex's REST API
   (`api.tcgdex.net/v2/{lang}/sets...`) by set code first, then by name,
   checking the Japanese dataset before English (Japan-exclusive sets often
   aren't in the English one at all). Returns a plain dict of whatever
   fields it found — callers merge it in only where the corresponding CLI
   flag wasn't explicitly given, so any field can be overridden by hand.
   Main Set/Secret Rares stat rows are derived from TCGdex's `cardCount`
   (`official` = main-set count, `total` = grand total including secret
   rares/alt arts). Prints a running commentary of what it's checking and
   found at each step (not gated behind `-v`); `-v` adds lower-level
   HTTP-failure detail on top. `_is_latin_text` guards the `era` field
   specifically, since it's drawn with a Latin-only italic font and a raw
   Japanese series name would render as tofu boxes.

4. **`build_cover(cfg)`** — the actual layout engine, driven by a single
   `argparse.Namespace` (`cfg`) of CLI flags. All positions/sizes are
   computed as fractions of `cfg.size` (the canvas is always square) so the
   whole design scales cleanly at any resolution. The key trick is the inner
   `run(y0, render)` closure: it's called once with `render=False` to
   measure the total content height (nothing is drawn, only `y` is
   accumulated), then again with `render=True` at a `start_y` computed to
   vertically center that content inside the panel. Any change to the
   layout (adding a block, changing a font size) must keep the two passes
   consistent — every branch that advances `y` must do so identically in
   both passes, and drawing should only happen inside `if render:` guards.

   Within `run()`, the layout goes: header → era subheading → rule → big
   set-code → set name (+ optional Japanese name) → rule → release
   date / total cards two-column grid → rule → left column (up to 2
   `--stat` rows) alongside a right column that is *either* a QR code block
   (`--qr-url`) *or* a circular completion gauge (`--completion`) *or*
   blank — QR takes priority if both are given → rule → footer with Poke
   Ball icon.

`main()` parses args, defaults `--out` to `<SET_CODE>_<name>_<EN|JP>_cover.png`
(`JP` if a Japanese name ends up on the cover, `EN` otherwise), and saves the PNG.

## Conventions specific to this codebase

- All user-facing text drawn on the cover is uppercased and rendered with
  `draw_spaced`/`measure_spaced` for consistent letter-spacing — don't use
  plain `draw.text` for label/header/footer strings.
- Sizes, margins, and offsets in `build_cover` are almost all expressed as
  `int(W * <fraction>)` rather than hardcoded pixels, to keep the design
  proportional across `--size` values. Follow this pattern for any new
  layout constants instead of hardcoding pixels at the 3600px scale.
- Fonts are always resolved through `get_font`/`fit_font` by role, never
  loaded directly by path, so the bundled-font / system-fallback / dry-run
  behavior stays consistent.
